#!/usr/bin/env python3
"""
server.py — local web backend for the 8-bit → 10-bit converter.

Why a browser UI: the macOS system Tk is deprecated and renders blank, so a
Tkinter GUI is unreliable. This serves a small local web app instead — no GUI
toolkit, no extra dependencies (Python stdlib only), and the bundled ffmpeg.

Run:  python3 server.py   (Start_Here.command does this and opens your browser)

Endpoints (all on 127.0.0.1):
    GET  /                     -> the app (index.html)
    POST /api/pick-files       -> native file dialog, returns chosen paths
    POST /api/pick-folder      -> native folder dialog, returns video paths
    GET  /api/settings         -> saved settings
    POST /api/settings         -> save settings
    POST /api/convert          -> start a batch {items, mode, strength}
    GET  /api/status           -> live batch status (poll this)
    POST /api/cancel           -> stop the running batch
    POST /api/scopes           -> render source-vs-processed scopes for a file
    GET  /api/scope?token&which-> serve a generated scope PNG
"""

import json
import os
import platform
import shutil
import subprocess
import threading
import time
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", 8766
INTAKE_DIR = tempfile.mkdtemp(prefix="10bit_intake_")  # dropped/uploaded files land here
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts")
STRENGTH_THR = {"Low": "0.01", "Medium": "0.02", "High": "0.05", "Custom": None}
X265_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]
SETTINGS_PATH = os.path.join(HERE, ".10bit_converter_settings.json")
DEFAULT_SETTINGS = {
    "dest_mode": "same", "dest_dir": "", "suffix": "_10bit", "on_exists": "skip",
    "crf": 18, "preset": "slow", "deband_range": 16, "deband_blur": True,
    "dither": 2, "thr_custom": "0.03",
    "target_mbps": 12.0,       # used when Bitrate = Custom
    "deflicker": False,        # ffmpeg deflicker filter (luminance flicker)
    "max_quality": False,      # process deband/dither at 16-bit for cleaner gradients
    "audio": "copy",           # "copy" (lossless, no re-encode) or "aac"
}

MP4_COPY_AUDIO = {"aac", "mp3", "ac3", "eac3"}   # codecs safe to stream-copy into .mp4


# ------------------------------------------------------------------ ffmpeg setup
def ensure_bundled_ffmpeg():
    d = os.path.join(HERE, "bin", platform.machine())
    if os.path.isfile(os.path.join(d, "ffmpeg")) and os.path.isfile(os.path.join(d, "ffprobe")):
        try:
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", d], capture_output=True)
        except Exception:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    return s


def save_settings(s):
    clean = {k: s.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS}
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(clean, f, indent=2)
    except Exception:
        pass
    return clean


def deband_noise_chain(thr, rng=16, blur=True, dither=2, deflicker=False, max_quality=False):
    chain = ""
    if deflicker:
        chain += "deflicker,"
    if max_quality:
        # Work at 16-bit 4:4:4 so debanding + dithering compute with headroom,
        # then the final format step quantizes to 10-bit. Pure precision — it
        # does not add, remove, or reinterpret any detail/texture.
        chain += "format=yuv444p16le,"
    return (chain + f"deband=1thr={thr}:2thr={thr}:3thr={thr}:range={rng}:blur={1 if blur else 0},"
            f"noise=alls={dither}:allf=t+u")


def build_filters(thr, pix_fmt, rng=16, blur=True, dither=2, deflicker=False, max_quality=False):
    return deband_noise_chain(thr, rng, blur, dither, deflicker, max_quality) + f",format={pix_fmt}"


def probe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True,
                             check=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def probe_bitrate_kbps(path):
    """Source video bitrate in kbps. Tries the video stream, falls back to the
    container total, then estimates from filesize/duration. 0 if unknown."""
    for args in (["-select_streams", "v:0", "-show_entries", "stream=bit_rate"],
                 ["-show_entries", "format=bit_rate"]):
        try:
            out = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "csv=p=0", path],
                                 capture_output=True, text=True, check=True).stdout.strip()
            if out.isdigit() and int(out) > 0:
                return int(out) // 1000
        except Exception:
            pass
    try:
        dur = probe_duration(path)
        if dur > 0:
            return int(os.path.getsize(path) * 8 / dur / 1000)
    except Exception:
        pass
    return 0


def probe_pix_fmt(path):
    try:
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", path],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def probe_audio_codec(path):
    try:
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                               "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def audio_args(in_path, is_prores, audio_mode):
    """No needless re-encode: copy the audio stream when safe, else transcode."""
    codec = probe_audio_codec(in_path)
    if not codec:
        return ["-an"]                      # source has no audio
    if audio_mode == "aac" and not is_prores:
        return ["-c:a", "aac", "-b:a", "192k"]
    if is_prores:
        return ["-c:a", "copy"]             # .mov accepts most codecs
    if codec in MP4_COPY_AUDIO:             # .mp4 copy only when compatible
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]  # e.g. PCM in a MOV -> can't copy to mp4


def color_args(in_path):
    """Carry the source's colour tags (primaries/transfer/matrix/range) into the
    output so editors interpret it correctly instead of guessing."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries",
                              "stream=color_primaries,color_transfer,color_space,color_range",
                              "-of", "default=noprint_wrappers=1:nokey=0", in_path],
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        return []
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    bad = ("", "unknown", "N/A", "reserved")
    args = []
    if d.get("color_primaries") not in bad:
        args += ["-color_primaries", d["color_primaries"]]
    if d.get("color_transfer") not in bad:
        args += ["-color_trc", d["color_transfer"]]
    if d.get("color_space") not in bad:
        args += ["-colorspace", d["color_space"]]
    if d.get("color_range") in ("tv", "pc", "limited", "full"):
        args += ["-color_range", d["color_range"]]
    return args


def pixfmt_bits(pf):
    for b in ("16", "12", "10"):
        if b in pf:
            return int(b)
    return 8


def human_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def verify_output(path):
    pf = probe_pix_fmt(path)
    bits = pixfmt_bits(pf)
    kbps = probe_bitrate_kbps(path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    check = "✓" if bits >= 10 else "⚠"
    return f"{check} {bits}-bit ({pf}) · {kbps / 1000:.1f} Mbps · {human_size(size)}"


def make_output_path(in_path, is_prores, dest_dir, suffix):
    base = os.path.splitext(os.path.basename(in_path))[0]
    ext = "mov" if is_prores else "mp4"
    if dest_dir:
        directory = dest_dir
    elif in_path.startswith(INTAKE_DIR):
        # Dropped/uploaded file has no real source folder -> deliver to Downloads.
        directory = os.path.expanduser("~/Downloads")
    else:
        directory = os.path.dirname(in_path)
    return os.path.join(directory, f"{base}{suffix}.{ext}")


def fmt_time(sec):
    if sec is None or sec < 0 or sec != sec:
        return "--:--"
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def osascript(script):
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def pick_files():
    script = (
        'set out to ""\n'
        'try\n'
        '  set theFiles to choose file with prompt "Select video(s) to convert" '
        'with multiple selections allowed\n'
        '  repeat with f in theFiles\n'
        '    set out to out & POSIX path of f & linefeed\n'
        '  end repeat\n'
        'end try\n'
        'return out'
    )
    raw = osascript(script)
    return [p for p in raw.splitlines() if p.strip()]


def pick_folder():
    p = osascript('try\nreturn POSIX path of (choose folder with prompt '
                  '"Select a folder of videos")\nend try')
    if not p or not os.path.isdir(p):
        return []
    return [os.path.join(p, f) for f in sorted(os.listdir(p))
            if os.path.isfile(os.path.join(p, f)) and f.lower().endswith(VIDEO_EXTS)
            and "_10bit." not in f]


# ------------------------------------------------------------------ conversion job
class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.running = False
        self.cancel = threading.Event()
        self.items = []          # [{path,name,status,pct}]
        self.now = {"file": "—", "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--"}
        self.summary = None

    def snapshot(self):
        with self.lock:
            return {"running": self.running, "items": list(self.items),
                    "now": dict(self.now), "summary": self.summary}


JOB = Job()


def hevc_rate_args(rate, settings, in_path):
    """Video-rate args for HEVC based on the chosen Bitrate mode."""
    crf = str(settings["crf"])
    if rate == "Match source":
        kbps = probe_bitrate_kbps(in_path)
        if kbps > 0:
            return ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.45)}k",
                    "-bufsize", f"{kbps * 2}k"]
        return ["-crf", crf]   # unknown source bitrate -> fall back to quality
    if rate == "Custom":
        kbps = int(float(settings.get("target_mbps", 12.0)) * 1000)
        return ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.45)}k",
                "-bufsize", f"{kbps * 2}k"]
    return ["-crf", crf]       # "Quality (CRF)"


def run_batch(items, mode, strength, rate, settings):
    thr = STRENGTH_THR.get(strength)
    if thr is None:
        thr = str(settings.get("thr_custom", "0.03"))
    is_prores = mode.startswith("ProRes")
    dest_dir = settings["dest_dir"] if settings["dest_mode"] == "custom" else ""
    suffix = settings["suffix"] or "_10bit"
    overwrite = settings["on_exists"] == "overwrite"
    pix_fmt = "yuv444p10le" if is_prores else "yuv420p10le"
    filters = build_filters(thr, pix_fmt, settings["deband_range"],
                            settings["deband_blur"], settings["dither"],
                            settings.get("deflicker", False),
                            settings.get("max_quality", False))
    total = len(items)
    done = failed = skipped = 0
    last_output = None

    for idx, it in enumerate(items, start=1):
        if JOB.cancel.is_set():
            break
        in_path = it["path"]
        out_path = make_output_path(in_path, is_prores, dest_dir, suffix)
        if dest_dir and not os.path.isdir(dest_dir):
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError:
                pass
        if os.path.exists(out_path) and not overwrite:
            skipped += 1
            _set_item(idx - 1, status="Skipped", pct="—")
            continue
        aud = audio_args(in_path, is_prores, settings.get("audio", "copy"))
        col = color_args(in_path)
        if is_prores:
            cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                   "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                   *(["-c:a", "pcm_s16le"] if aud == ["-an"] else aud), *col,
                   "-progress", "pipe:1", out_path]
        else:
            cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                   "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                   *hevc_rate_args(rate, settings, in_path),
                   "-preset", str(settings["preset"]), "-tag:v", "hvc1",
                   *aud, *col, "-progress", "pipe:1", out_path]
        src_bits = pixfmt_bits(probe_pix_fmt(in_path))
        dur = probe_duration(in_path)
        _set_item(idx - 1, status="Running", pct="0%")
        with JOB.lock:
            JOB.now = {"file": f"[{idx}/{total}]  {it['name']}", "pct": 0,
                       "frame": "", "fps": "", "speed": "", "eta": "--:--"}
        err = _run_ffmpeg(cmd, dur, idx - 1)
        if JOB.cancel.is_set():
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            _set_item(idx - 1, status="Cancelled", pct="—")
            break
        if err is not None:
            failed += 1
            _set_item(idx - 1, status="Failed", pct="—", error=err)
            continue
        done += 1
        last_output = out_path
        info = verify_output(out_path)
        if src_bits >= 10:
            info = f"source was {src_bits}-bit (deband only) · " + info
        _set_item(idx - 1, status="Done", pct="100%", info=info, out=out_path)

    cancelled = JOB.cancel.is_set()
    parts = [f"{done} done"]
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        parts.append(f"{failed} failed")
    if cancelled:
        parts.append("cancelled")
    with JOB.lock:
        JOB.running = False
        JOB.summary = "Finished: " + ", ".join(parts) + "."
        JOB.now = {"file": "—", "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--"}
    if last_output and not cancelled:
        subprocess.run(["open", "-R", last_output])


def _set_item(i, **kw):
    with JOB.lock:
        if 0 <= i < len(JOB.items):
            JOB.items[i].update(kw)


def _run_ffmpeg(cmd, dur, item_idx):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)

    def _watch():
        while proc.poll() is None:
            if JOB.cancel.wait(0.2):
                proc.kill()
                return
    threading.Thread(target=_watch, daemon=True).start()

    cur = {}
    for line in proc.stdout:
        if JOB.cancel.is_set():
            proc.kill()
            break
        line = line.strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        cur[k] = v
        if k == "progress":
            _emit(cur, dur, item_idx)
            cur = {}
    proc.wait()
    if proc.returncode not in (0, None) and not JOB.cancel.is_set():
        err = proc.stderr.read() if proc.stderr else ""
        return err[-600:] if err else f"ffmpeg exited with code {proc.returncode}"
    return None


def _emit(cur, dur, item_idx):
    us = cur.get("out_time_us") or cur.get("out_time_ms")
    secs = int(us) / 1_000_000 if (us and us.isdigit()) else 0.0
    pct = min(100.0, secs / dur * 100) if dur > 0 else 0.0
    speed = cur.get("speed", "")
    eta = None
    try:
        spd = float(str(speed).rstrip("x"))
        if spd > 0 and dur > 0:
            eta = max(0.0, (dur - secs) / spd)
    except ValueError:
        pass
    if cur.get("progress") == "end":
        pct = 100.0
        eta = 0.0
    with JOB.lock:
        JOB.now = {"file": JOB.now.get("file", "—"), "pct": round(pct),
                   "frame": cur.get("frame", ""), "fps": cur.get("fps", ""),
                   "speed": speed, "eta": fmt_time(eta)}
        if 0 <= item_idx < len(JOB.items):
            JOB.items[item_idx]["pct"] = f"{pct:.0f}%"


# ------------------------------------------------------------------ scopes
SCOPES = {}   # token -> dir


def render_scopes(in_path, strength, settings):
    thr = STRENGTH_THR.get(strength) or str(settings.get("thr_custom", "0.03"))
    chain = deband_noise_chain(thr, settings["deband_range"], settings["deband_blur"],
                               settings["dither"], settings.get("deflicker", False),
                               settings.get("max_quality", False))
    outdir = tempfile.mkdtemp(prefix="scopes_")
    dur = probe_duration(in_path)
    t = max(0.0, dur * 0.4) if dur else 1.0
    jobs = {
        "src_thumb": "scale=440:-2",
        "src_hist": "histogram=level_height=170,scale=440:-2",
        "aft_thumb": f"{chain},scale=440:-2",
        "aft_hist": f"{chain},histogram=level_height=170,scale=440:-2",
    }
    for which, vf in jobs.items():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", in_path,
                        "-frames:v", "1", "-vf", vf, os.path.join(outdir, which + ".png")],
                       capture_output=True)
    token = os.path.basename(outdir)
    SCOPES[token] = outdir
    return token


COMPARE = {}   # token -> dir


def render_compare(src, out, t):
    d = tempfile.mkdtemp(prefix="cmp_")
    for which, path in (("before", src), ("after", out)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", path,
                        "-frames:v", "1", "-vf", "scale=1000:-2",
                        os.path.join(d, which + ".png")], capture_output=True)
    token = os.path.basename(d)
    COMPARE[token] = d
    return token


# ------------------------------------------------------------------ HTTP handler
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, "index.html missing", "text/plain")
        elif u.path == "/api/status":
            self._send(200, JOB.snapshot())
        elif u.path == "/api/settings":
            self._send(200, load_settings())
        elif u.path in ("/api/scope", "/api/compare-image"):
            q = urllib.parse.parse_qs(u.query)
            token = q.get("token", [""])[0]
            which = q.get("which", [""])[0]
            store = SCOPES if u.path == "/api/scope" else COMPARE
            d = store.get(token)
            fp = os.path.join(d, os.path.basename(which) + ".png") if d else None
            if fp and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, "not found", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/pick-files":
            paths = pick_files()
            self._send(200, [{"path": p, "name": os.path.basename(p)}
                             for p in paths if p.lower().endswith(VIDEO_EXTS)])
        elif u.path == "/api/pick-folder":
            paths = pick_folder()
            self._send(200, [{"path": p, "name": os.path.basename(p)} for p in paths])
        elif u.path == "/api/upload":
            q = urllib.parse.parse_qs(u.query)
            name = os.path.basename(q.get("name", ["dropped"])[0]) or "dropped"
            if not name.lower().endswith(VIDEO_EXTS):
                self._send(400, {"error": "not a video"})
                return
            dest = os.path.join(INTAKE_DIR, name)
            stem, ext = os.path.splitext(name)
            i = 1
            while os.path.exists(dest):
                dest = os.path.join(INTAKE_DIR, f"{stem}_{i}{ext}")
                i += 1
            n = int(self.headers.get("Content-Length", 0))
            try:
                with open(dest, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
            except Exception as e:
                self._send(500, {"error": str(e)})
                return
            self._send(200, {"path": dest, "name": os.path.basename(dest)})
        elif u.path == "/api/settings":
            self._send(200, save_settings(self._read_json()))
        elif u.path == "/api/convert":
            data = self._read_json()
            items = [{"path": it["path"], "name": it.get("name", os.path.basename(it["path"])),
                      "status": "Queued", "pct": ""} for it in data.get("items", [])
                     if it.get("path")]
            if not items:
                self._send(400, {"error": "no items"})
                return
            if JOB.running:
                self._send(409, {"error": "already running"})
                return
            with JOB.lock:
                JOB.reset()
                JOB.running = True
                JOB.items = items
            threading.Thread(target=run_batch, args=(items, data.get("mode", "HEVC"),
                             data.get("strength", "Medium"), data.get("rate", "Match source"),
                             load_settings()), daemon=True).start()
            self._send(200, {"ok": True})
        elif u.path == "/api/cancel":
            JOB.cancel.set()
            self._send(200, {"ok": True})
        elif u.path == "/api/scopes":
            data = self._read_json()
            path = data.get("path")
            if not path or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            token = render_scopes(path, data.get("strength", "Medium"), load_settings())
            self._send(200, {"token": token, "name": os.path.basename(path)})
        elif u.path == "/api/compare":
            data = self._read_json()
            src, out = data.get("src"), data.get("out")
            if not (src and out and os.path.isfile(src) and os.path.isfile(out)):
                self._send(400, {"error": "files not found"})
                return
            dur = probe_duration(src)
            t = data.get("t")
            if t is None:
                t = round(dur * 0.4, 2) if dur else 1.0
            t = float(t)
            if dur:
                t = max(0.0, min(t, max(0.0, dur - 0.05)))
            token = render_compare(src, out, t)
            self._send(200, {"token": token, "duration": dur, "t": t})
        else:
            self._send(404, {"error": "unknown"})


def main():
    ensure_bundled_ffmpeg()
    if not shutil.which("ffmpeg"):
        print("WARNING: ffmpeg not found (bundle missing and none on PATH).")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"8-bit → 10-bit converter running at {url}")
    print("Leave this window open while you use the app. Close it (or Ctrl-C) to quit.")
    try:
        subprocess.Popen(["open", url])
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
