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

import atexit
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
    "denoise": "off",          # "off" | "light" | "medium" (hqdn3d; softens slightly)
    "two_pass": False,         # 2-pass HEVC for accurate target bitrate (Match/Custom)
    "dual_export": False,      # when Format=ProRes, also produce an HEVC preview alongside it
}

DENOISE_FILTER = {"light": "hqdn3d=2:1:2:3", "medium": "hqdn3d=4:3:6:6"}

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


CUSTOM_PRESETS_PATH = os.path.join(HERE, ".10bit_custom_presets.json")
# What a preset captures: the top-level Format/Deband/Bitrate picks plus every
# processing setting (everything except output-location fields, which are
# per-machine and shouldn't travel with a preset).
PRESET_SETTINGS_KEYS = ["crf", "preset", "deband_range", "deband_blur", "dither", "thr_custom",
                        "target_mbps", "deflicker", "max_quality", "audio", "denoise",
                        "two_pass", "dual_export"]


def load_custom_presets():
    try:
        with open(CUSTOM_PRESETS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_custom_presets(presets):
    try:
        with open(CUSTOM_PRESETS_PATH, "w") as f:
            json.dump(presets, f, indent=2)
    except Exception:
        pass


def deband_noise_chain(thr, rng=16, blur=True, dither=2, deflicker=False, max_quality=False,
                       denoise="off"):
    chain = ""
    if denoise in DENOISE_FILTER:
        chain += DENOISE_FILTER[denoise] + ","
    if deflicker:
        chain += "deflicker,"
    if max_quality:
        # Work at 16-bit 4:4:4 so debanding + dithering compute with headroom,
        # then the final format step quantizes to 10-bit. Pure precision — it
        # does not add, remove, or reinterpret any detail/texture.
        chain += "format=yuv444p16le,"
    return (chain + f"deband=1thr={thr}:2thr={thr}:3thr={thr}:range={rng}:blur={1 if blur else 0},"
            f"noise=alls={dither}:allf=t+u")


def build_filters(thr, pix_fmt, rng=16, blur=True, dither=2, deflicker=False, max_quality=False,
                  denoise="off"):
    return (deband_noise_chain(thr, rng, blur, dither, deflicker, max_quality, denoise)
            + f",format={pix_fmt}")


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


def _column_banding_score(raw, w, h, min_run=3, min_change=6, win=15, agree_frac=0.65, step=8):
    """Fraction of sampled pixels that sit in a monotonic, staircase-like
    plateau: a run of >=min_run identical values flanked by different values
    (a 'step'), where the surrounding window mostly trends in the same
    direction (rules out noisy texture/grass/crowd, which flips direction
    often) and spans a real overall change (rules out flat solid colour).
    This is the direct visual definition of banding: a gradient rendered as
    discrete steps instead of a continuous ramp."""
    banded = 0
    total = 0
    for c in range(0, w, step):
        col = raw[c:c + w * h:w]  # bytes object supports slicing with a stride
        i = 0
        while i < h:
            j = i
            v = col[i]
            while j < h and col[j] == v:
                j += 1
            run = j - i
            if run >= min_run:
                left = col[i - 1] if i > 0 else v
                right = col[j] if j < h else v
                if left != v and right != v:
                    direction = 1 if right > left else (-1 if right < left else 0)
                    lo, hi = max(0, i - win), min(h, j + win)
                    window = col[lo:hi]
                    span = max(window) - min(window)
                    diffs = [window[k + 1] - window[k] for k in range(len(window) - 1)]
                    agree = sum(1 for d in diffs if d == 0 or (direction != 0 and (d > 0) == (direction > 0)))
                    frac = agree / len(diffs) if diffs else 0
                    if span >= min_change and direction != 0 and frac >= agree_frac:
                        banded += run
            total += run
            i = j
    return (banded / total * 100) if total else 0.0


def analyze_banding(path, samples=4):
    """Heuristic (non-ML) estimate of how much visible 8-bit banding a clip
    likely has, so users can skip conversion on already-clean footage. This
    is a triage signal, not a precise measurement — always cross-check with
    Preview scopes / Compare on the actual clip."""
    dur = probe_duration(path)
    info = probe_info(path)
    sw, sh = info.get("width") or 0, info.get("height") or 0
    if not sw or not sh:
        return {"score": 0, "band": "unknown", "message": "Couldn't read this file's resolution.", "samples": 0}
    # Cap analysis width so a 4K+ source doesn't blow up runtime. Nearest-
    # neighbour scaling preserves exact step edges — bilinear would blur them
    # away and destroy the very signal we're detecting (this bit us during
    # development). Computed in Python (not read back from ffprobe, which has
    # no -vf option) so it always matches ffmpeg's own scale=W:-2 rounding.
    if sw > 1920:
        w = 1920
        h = round(sh * 1920 / sw / 2) * 2
    else:
        w, h = sw, sh
    times = [round(dur * (i + 0.5) / samples, 2) for i in range(samples)] if dur else [1.0]
    scores = []
    for t in times:
        vf = f"format=gray,scale={w}:{h}:flags=neighbor"
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path,
                            "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"],
                           capture_output=True)
        if r.returncode != 0 or not r.stdout or len(r.stdout) != w * h:
            continue  # skip this sample rather than misread bytes on a mismatch
        scores.append(_column_banding_score(r.stdout, w, h))
    if not scores:
        return {"score": 0, "band": "unknown", "message": "Couldn't analyze this file.", "samples": 0}
    score = sum(scores) / len(scores)
    if score < 10:
        band, message = "low", ("Low banding detected — this footage already looks fairly smooth. "
                                 "10-bit conversion will still add grading headroom and a touch of "
                                 "dither, but don't expect a dramatic visible change.")
    elif score < 35:
        band, message = "moderate", ("Some banding detected — a Medium or High deband pass should "
                                      "show a visible improvement in gradients (skies, smoke, gradients).")
    else:
        band, message = "high", ("Significant banding detected — this footage should show a clear "
                                  "improvement after conversion. Check Preview scopes to confirm.")
    return {"score": round(score, 1), "band": band, "message": message, "samples": len(scores)}


def verify_output(path):
    pf = probe_pix_fmt(path)
    bits = pixfmt_bits(pf)
    kbps = probe_bitrate_kbps(path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    check = "✓" if bits >= 10 else "⚠"
    return f"{check} {bits}-bit ({pf}) · {kbps / 1000:.1f} Mbps · {human_size(size)}"


def probe_info(path):
    """One-shot probe: duration, bitrate (kbps), resolution, fps, pix_fmt."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,pix_fmt,avg_frame_rate:format=duration,bit_rate",
             "-of", "json", path], capture_output=True, text=True, check=True).stdout
        d = json.loads(out)
        st = (d.get("streams") or [{}])[0]
        fmt = d.get("format", {})
        dur = float(fmt.get("duration") or 0)
        br = fmt.get("bit_rate")
        if br and br.isdigit() and int(br) > 0:
            kbps = int(br) // 1000
        elif dur > 0:
            kbps = int(os.path.getsize(path) * 8 / dur / 1000)
        else:
            kbps = 0
        fps = 0.0
        r = st.get("avg_frame_rate", "0/0")
        if "/" in r:
            n, dn = r.split("/")
            fps = (float(n) / float(dn)) if float(dn or 0) else 0.0
        return {"dur": round(dur, 2), "kbps": kbps, "width": st.get("width", 0),
                "height": st.get("height", 0), "fps": round(fps, 2),
                "pix_fmt": st.get("pix_fmt", "")}
    except Exception:
        return {"dur": 0, "kbps": 0, "width": 0, "height": 0, "fps": 0, "pix_fmt": ""}


def item_for(path):
    return {"path": path, "name": os.path.basename(path), **probe_info(path)}


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
        self.total = 0
        self.index = 0
        self.started = 0.0

    def snapshot(self):
        with self.lock:
            overall = 0.0
            eta = "--:--"
            if self.running and self.total:
                overall = min(100.0, ((self.index - 1) + self.now["pct"] / 100) / self.total * 100)
                el = time.time() - self.started if self.started else 0
                if overall > 1 and el > 0:
                    eta = fmt_time(el / (overall / 100) - el)
            return {"running": self.running, "items": list(self.items),
                    "now": dict(self.now), "summary": self.summary,
                    "batch": {"index": self.index, "total": self.total,
                              "overall": round(overall), "eta": eta}}


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
                            settings.get("max_quality", False),
                            settings.get("denoise", "off"))
    total = len(items)
    done = failed = skipped = 0
    last_output = None
    with JOB.lock:
        JOB.total = total
        JOB.started = time.time()

    try:
        for idx, it in enumerate(items, start=1):
            if JOB.cancel.is_set():
                break
            try:
                with JOB.lock:
                    JOB.index = idx
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
                pre_cmd = None
                if is_prores:
                    cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                           "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                           *(["-c:a", "pcm_s16le"] if aud == ["-an"] else aud), *col,
                           "-progress", "pipe:1", out_path]
                else:
                    rate_args = hevc_rate_args(rate, settings, in_path)
                    base = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                            "-c:v", "libx265", "-pix_fmt", "yuv420p10le", *rate_args,
                            "-preset", str(settings["preset"])]
                    # Two-pass only makes sense with a bitrate target (not CRF).
                    two_pass = bool(settings.get("two_pass")) and "-b:v" in rate_args
                    if two_pass:
                        stats = os.path.join(INTAKE_DIR, f"2pass_{idx}.log")
                        pre_cmd = [*base, "-x265-params", f"pass=1:stats={stats}", "-an",
                                   "-f", "null", "-progress", "pipe:1", os.devnull]
                        cmd = [*base, "-x265-params", f"pass=2:stats={stats}", "-tag:v", "hvc1",
                               *aud, *col, "-progress", "pipe:1", out_path]
                    else:
                        cmd = [*base, "-tag:v", "hvc1", *aud, *col, "-progress", "pipe:1", out_path]
                src_bits = pixfmt_bits(probe_pix_fmt(in_path))
                dur = probe_duration(in_path)
                name = it["name"]
                _set_item(idx - 1, status="Running", pct="0%")

                # Pass 1 (two-pass only): analysis pass, discarded output.
                if pre_cmd is not None:
                    with JOB.lock:
                        JOB.now = {"file": f"[{idx}/{total}]  {name} — pass 1/2 (analyzing)",
                                   "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--"}
                    e1 = _run_ffmpeg(pre_cmd, dur, idx - 1)
                    if not JOB.cancel.is_set() and e1 is not None:
                        failed += 1
                        _set_item(idx - 1, status="Failed", pct="—", error=e1)
                        continue

                err = None
                if not JOB.cancel.is_set():
                    with JOB.lock:
                        JOB.now = {"file": f"[{idx}/{total}]  {name}" + (" — pass 2/2" if pre_cmd else ""),
                                   "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--"}
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

                # Dual export: alongside the ProRes master, also cut a small
                # HEVC preview from the same filter chain — useful for quick
                # review/sharing without opening the (huge) grading master.
                if is_prores and settings.get("dual_export") and not JOB.cancel.is_set():
                    preview_path = make_output_path(in_path, False, dest_dir, suffix + "_preview")
                    if not (os.path.exists(preview_path) and not overwrite):
                        with JOB.lock:
                            JOB.now = {"file": f"[{idx}/{total}]  {name} — HEVC preview",
                                       "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--"}
                        pv_aud = audio_args(in_path, False, settings.get("audio", "copy"))
                        pv_cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                                  "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-crf", "20",
                                  "-preset", "veryfast", "-tag:v", "hvc1", *pv_aud, *col,
                                  "-progress", "pipe:1", preview_path]
                        # item_idx=-1: the preview isn't tracked as its own row,
                        # so route progress updates only to JOB.now, not a row.
                        pv_err = _run_ffmpeg(pv_cmd, dur, -1)
                        if pv_err is None and os.path.isfile(preview_path):
                            info += f" · preview: {os.path.basename(preview_path)} ({verify_output(preview_path)})"
                        else:
                            info += " · preview export failed"

                _set_item(idx - 1, status="Done", pct="100%", info=info, out=out_path)
            except Exception as e:
                # A crash in one item must not hang the batch forever (JOB.running
                # would otherwise never be reset) or take down the worker thread
                # silently — mark this item Failed and move on to the next.
                failed += 1
                _set_item(idx - 1, status="Failed", pct="—", error=f"Unexpected error: {e}")
                continue
    finally:
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
        if item_idx is not None and 0 <= item_idx < len(JOB.items):
            JOB.items[item_idx]["pct"] = f"{pct:.0f}%"


# ------------------------------------------------------------------ scopes
SCOPES = {}   # token -> dir
FILMSTRIPS = {}  # token -> dir


def render_scopes(in_path, strength, settings, t=None):
    thr = STRENGTH_THR.get(strength) or str(settings.get("thr_custom", "0.03"))
    chain = deband_noise_chain(thr, settings["deband_range"], settings["deband_blur"],
                               settings["dither"], settings.get("deflicker", False),
                               settings.get("max_quality", False), settings.get("denoise", "off"))
    outdir = tempfile.mkdtemp(prefix="scopes_")
    dur = probe_duration(in_path)
    if t is None:
        t = max(0.0, dur * 0.4) if dur else 1.0
    t = max(0.0, min(float(t), max(0.0, dur - 0.05))) if dur else float(t)
    # The `histogram`/`waveform` filters can SIGSEGV on some pixel formats
    # (e.g. the 16-bit 4:4:4 frame Max quality inserts, or raw RGBA). Preview
    # scopes are for visual comparison only, so downconvert to 8-bit right
    # before them — this doesn't affect the "after" thumbnail or the real
    # encode, only this diagnostic view.
    jobs = {
        "src_thumb": "scale=440:-2",
        "src_hist": "histogram=level_height=170,scale=440:-2",
        "src_wave": "format=yuv420p,waveform=mode=column:intensity=0.6,scale=440:-2",
        "aft_thumb": f"{chain},scale=440:-2",
        "aft_hist": f"{chain},format=yuv420p,histogram=level_height=170,scale=440:-2",
        "aft_wave": f"{chain},format=yuv420p,waveform=mode=column:intensity=0.6,scale=440:-2",
    }
    errors = {}
    for which, vf in jobs.items():
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", in_path,
                           "-frames:v", "1", "-vf", vf, os.path.join(outdir, which + ".png")],
                          capture_output=True, text=True)
        out_file = os.path.join(outdir, which + ".png")
        if r.returncode != 0 or not os.path.isfile(out_file):
            errors[which] = r.stderr.strip() or f"ffmpeg exited {r.returncode}"
    token = os.path.basename(outdir)
    SCOPES[token] = outdir
    return token, errors, t, dur


def render_filmstrip(in_path, count=8):
    """N evenly-spaced thumbnails across the clip so the user can click to
    pick the frame most likely to show banding, instead of a fixed timestamp."""
    outdir = tempfile.mkdtemp(prefix="strip_")
    dur = probe_duration(in_path)
    times = []
    if dur > 0:
        for i in range(count):
            times.append(round(dur * (i + 0.5) / count, 2))
    else:
        times = [1.0]
    for i, t in enumerate(times):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", in_path,
                        "-frames:v", "1", "-vf", "scale=160:-2",
                        os.path.join(outdir, f"f{i}.png")], capture_output=True)
    token = os.path.basename(outdir)
    FILMSTRIPS[token] = outdir
    return token, times


COMPARE = {}   # token -> dir


def render_compare(src, out, t, zoom=None):
    """zoom: optional {cx, cy, factor} — cx/cy are 0..1 fractions of frame
    (center of the region to zoom into), factor is how much to zoom in (e.g. 3
    = view 1/3 width/height, magnified back up to display size). Banding is
    usually only visible in a small region, so a full-frame view often hides it."""
    d = tempfile.mkdtemp(prefix="cmp_")
    crop_vf = ""
    if zoom and zoom.get("factor", 1) > 1:
        f = max(1.0, float(zoom["factor"]))
        cx, cy = float(zoom.get("cx", 0.5)), float(zoom.get("cy", 0.5))
        w_frac, h_frac = 1.0 / f, 1.0 / f
        x_frac = max(0.0, min(1 - w_frac, cx - w_frac / 2))
        y_frac = max(0.0, min(1 - h_frac, cy - h_frac / 2))
        crop_vf = (f"crop=iw*{w_frac}:ih*{h_frac}:iw*{x_frac}:ih*{y_frac},")
    for which, path in (("before", src), ("after", out)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", path,
                        "-frames:v", "1", "-vf", f"{crop_vf}scale=1000:-2",
                        os.path.join(d, which + ".png")], capture_output=True)
    # Amplified difference view: |after - before| * gain, so genuinely-changed
    # pixels (deband/dither) stand out. Honestly labelled as exaggerated in the UI.
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-ss", f"{t}", "-i", src, "-ss", f"{t}", "-i", out,
                    "-frames:v", "1", "-filter_complex",
                    f"[0:v]{crop_vf}scale=1000:-2[a];[1:v]{crop_vf}scale=1000:-2[b];"
                    f"[a][b]blend=all_mode=difference,lutrgb=r=val*8:g=val*8:b=val*8",
                    os.path.join(d, "diff.png")], capture_output=True)
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
        elif u.path == "/api/presets":
            self._send(200, load_custom_presets())
        elif u.path in ("/api/scope", "/api/compare-image", "/api/filmstrip-image"):
            q = urllib.parse.parse_qs(u.query)
            token = q.get("token", [""])[0]
            which = q.get("which", [""])[0]
            store = {"/api/scope": SCOPES, "/api/compare-image": COMPARE,
                     "/api/filmstrip-image": FILMSTRIPS}[u.path]
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
            self._send(200, [item_for(p) for p in paths if p.lower().endswith(VIDEO_EXTS)])
        elif u.path == "/api/pick-folder":
            paths = pick_folder()
            self._send(200, [item_for(p) for p in paths])
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
            self._send(200, item_for(dest))
        elif u.path == "/api/settings":
            self._send(200, save_settings(self._read_json()))
        elif u.path == "/api/presets/save":
            data = self._read_json()
            name = (data.get("name") or "").strip()
            if not name:
                self._send(400, {"error": "name required"})
                return
            entry = {
                "name": name,
                "mode": data.get("mode", "HEVC (smaller, delivery)"),
                "strength": data.get("strength", "Medium"),
                "rate": data.get("rate", "Match source"),
                "settings": {k: data.get("settings", {}).get(k) for k in PRESET_SETTINGS_KEYS
                            if k in data.get("settings", {})},
            }
            presets = [p for p in load_custom_presets() if p.get("name") != name]
            presets.append(entry)
            save_custom_presets(presets)
            self._send(200, {"ok": True, "presets": presets})
        elif u.path == "/api/presets/delete":
            name = (self._read_json().get("name") or "").strip()
            presets = [p for p in load_custom_presets() if p.get("name") != name]
            save_custom_presets(presets)
            self._send(200, {"ok": True, "presets": presets})
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
        elif u.path == "/api/reveal":
            path = self._read_json().get("path")
            if path and os.path.exists(path):
                subprocess.Popen(["open", "-R", path])
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        elif u.path == "/api/scopes":
            data = self._read_json()
            path = data.get("path")
            if not path or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            token, errors, t, dur = render_scopes(path, data.get("strength", "Medium"),
                                                   load_settings(), data.get("t"))
            self._send(200, {"token": token, "name": os.path.basename(path),
                             "errors": errors, "t": t, "duration": dur})
        elif u.path == "/api/filmstrip":
            data = self._read_json()
            path = data.get("path")
            if not path or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            token, times = render_filmstrip(path, int(data.get("count", 8)))
            self._send(200, {"token": token, "times": times})
        elif u.path == "/api/banding-meter":
            data = self._read_json()
            path = data.get("path")
            if not path or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            self._send(200, analyze_banding(path))
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
            token = render_compare(src, out, t, data.get("zoom"))
            self._send(200, {"token": token, "duration": dur, "t": t})
        else:
            self._send(404, {"error": "unknown"})


def cleanup_temp():
    """Remove intake uploads + scope/compare/filmstrip frame dirs on exit."""
    for d in [INTAKE_DIR, *SCOPES.values(), *COMPARE.values(), *FILMSTRIPS.values()]:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ensure_bundled_ffmpeg()
    atexit.register(cleanup_temp)
    if not shutil.which("ffmpeg"):
        print("WARNING: ffmpeg not found (bundle missing and none on PATH).")

    # Bind the first free port from PORT upward (survives a stale/second instance).
    srv = None
    port = PORT
    for p in range(PORT, PORT + 20):
        try:
            srv = ThreadingHTTPServer((HOST, p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print(f"Could not find a free port in {PORT}–{PORT + 19}. "
              f"Is another copy already running? Try that browser tab.")
        return

    url = f"http://{HOST}:{port}/"
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
