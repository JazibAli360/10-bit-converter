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
import math
import os
import plistlib
import secrets
import shutil
import struct
import sys
import subprocess
import threading
import time
import tempfile
import urllib.parse
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from export_safety import (resolve_output_path, staging_output_path,
                           unique_output_path, writable_parent)
from conversion_runner import run_ffmpeg
from conversion_service import ConversionPlanner
from watch_service import WatchService
from native_lifecycle import run_native_window
from single_instance import InstanceGuard, activate_existing
from history_store import append_report, read_reports
from preflight import estimate_export_bytes as estimate_export_bytes_pure
from preview_service import PreviewService
from engines import DEFAULT_ENGINE, LIBPLACEBO_ENGINE, engine_catalog, requested_engine
from media_probe import (pixfmt_bits, probe_audio_codec, probe_bitrate_kbps,
                         probe_duration, probe_info, probe_pix_fmt,
                         probe_subtitle_codecs)
from runtime_platform import (KeepAwake, app_support_dir, binary_platform_dir,
                              fallback_file_dialog, ffmpeg_tool_names, notify,
                              open_url, remove_macos_quarantine, reveal_files, IS_WINDOWS,
                              show_error)
from update_service import ISSUES_URL, RELEASES_URL, check_for_update

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", int(os.environ.get("TENBIT_PORT", "8766"))
INTAKE_DIR = tempfile.mkdtemp(prefix="10bit_intake_")  # dropped/uploaded files land here
APP_NAME = "10-bit Converter"
DEVELOPMENT_VERSION = "0.1.0"


def installed_app_version():
    """Read the release version from a frozen app's Info.plist."""
    if getattr(sys, "frozen", False):
        info_path = os.path.normpath(os.path.join(os.path.dirname(sys.executable), "..", "Info.plist"))
        try:
            with open(info_path, "rb") as handle:
                return str(plistlib.load(handle).get("CFBundleShortVersionString") or DEVELOPMENT_VERSION)
        except (OSError, plistlib.InvalidFileException):
            pass
    return DEVELOPMENT_VERSION


APP_VERSION = installed_app_version()
APP_SUPPORT_DIR = os.environ.get(
    "TENBIT_APP_DATA",
    app_support_dir("Jazib Ali 360", APP_NAME),
)
APP_BUNDLE_ID = "com.jazibali360.tenbitconverter"
INSTANCE_GUARD = None
BUNDLED_ENGINE_LOCK = threading.Lock()
API_TOKEN = secrets.token_urlsafe(32)
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts")
STRENGTH_THR = DEFAULT_ENGINE.strength_thresholds
X265_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]
SETTINGS_PATH = os.path.join(APP_SUPPORT_DIR, "settings.json")
WINDOW_STATE_PATH = os.path.join(APP_SUPPORT_DIR, "window_state.json")
UPDATE_STATE_PATH = os.path.join(APP_SUPPORT_DIR, "update_state.json")
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
    "live_preview": False,     # opt-in: source-frame updates cost an extra FFmpeg decode
    "colour_safe": False,      # high-precision, chroma-aware generated-footage path
    "source_interpretation": "preserve",  # preserve | rec709_limited | srgb_full
    "engine": "ffmpeg-deband-v1",
}

DENOISE_FILTER = {"light": "hqdn3d=2:1:2:3", "medium": "hqdn3d=4:3:6:6"}

MP4_COPY_AUDIO = {"aac", "mp3", "ac3", "eac3"}   # codecs safe to stream-copy into .mp4
CONVERSION_PLANNER = ConversionPlanner(INTAKE_DIR, STRENGTH_THR, DEFAULT_ENGINE)


# ------------------------------------------------------------------ ffmpeg setup
def bundled_ffmpeg_dir():
    """Return the app's own FFmpeg directory, only when it is runnable.

    The native app intentionally does not depend on Homebrew, PATH, or a
    separately installed FFmpeg. Keeping this check here makes a damaged or
    incomplete app bundle fail clearly instead of converting with an unknown
    system binary.
    """
    d = os.path.join(HERE, "bin", binary_platform_dir())
    tools = [os.path.join(d, name) for name in ffmpeg_tool_names()]
    return d if all(os.path.isfile(tool) and os.access(tool, os.X_OK) for tool in tools) else None


def bundled_engine_dir(engine=None):
    base = os.path.join(HERE, "bin", binary_platform_dir())
    if getattr(engine, "engine_id", "") == "libplacebo-deband-v1":
        bundle = os.path.join(base, "libplacebo.bundle.zip")
        if os.path.isfile(bundle):
            try:
                # The archive is the release artifact that preserves MoltenVK
                # metadata. Prefer it even in a developer checkout: an older
                # adjacent libplacebo folder may be present and has previously
                # made the GPU option appear unavailable.
                stat = os.stat(bundle)
                cache_root = os.path.join(
                    APP_SUPPORT_DIR, "engines",
                    f"libplacebo-{stat.st_size:x}-{stat.st_mtime_ns:x}",
                )
                extracted = os.path.join(cache_root, "libplacebo")
                tools = [os.path.join(extracted, name) for name in ffmpeg_tool_names()]
                with BUNDLED_ENGINE_LOCK:
                    if not all(os.path.isfile(tool) and os.access(tool, os.X_OK) for tool in tools):
                        staging = f"{cache_root}.extract-{uuid.uuid4().hex}"
                        os.makedirs(staging, exist_ok=False)
                        try:
                            # ditto preserves the code-signing/resource metadata embedded
                            # in MoltenVK and its dylibs. Python's ZipFile extraction does
                            # not, which can make a valid GPU runtime fail at launch.
                            if sys.platform == "darwin":
                                subprocess.run(["ditto", "-x", "-k", bundle, staging],
                                               check=True, capture_output=True, timeout=90)
                            else:
                                shutil.unpack_archive(bundle, staging, "zip")
                            staged_engine = os.path.join(staging, "libplacebo")
                            staged_tools = [os.path.join(staged_engine, name) for name in ffmpeg_tool_names()]
                            if not all(os.path.isfile(tool) for tool in staged_tools):
                                raise OSError("optional engine archive is incomplete")
                            os.makedirs(os.path.dirname(cache_root), exist_ok=True)
                            os.rename(staging, cache_root)
                        except Exception:
                            shutil.rmtree(staging, ignore_errors=True)
                            raise
                tools = [os.path.join(extracted, name) for name in ffmpeg_tool_names()]
                if all(os.path.isfile(tool) and os.access(tool, os.X_OK) for tool in tools):
                    for tool in tools:
                        os.chmod(tool, os.stat(tool).st_mode | 0o111)
                    return extracted
            except (OSError, subprocess.SubprocessError):
                return None
        # Direct folder path supports developer builds and the Windows package,
        # which deliberately ships folders instead of a user-facing zip.
        candidate = os.path.join(base, "libplacebo")
        tools = [os.path.join(candidate, name) for name in ffmpeg_tool_names()]
        if all(os.path.isfile(tool) and os.access(tool, os.X_OK) for tool in tools):
            if IS_WINDOWS:
                # Program Files is not writable for a normal user. Copy the
                # packaged GPU folder to LocalAppData before the engine writes
                # its per-user Vulkan ICD runtime manifest.
                try:
                    stat = os.stat(candidate)
                    cache_root = os.path.join(
                        APP_SUPPORT_DIR, "engines",
                        f"libplacebo-{stat.st_size:x}-{stat.st_mtime_ns:x}",
                    )
                    extracted = os.path.join(cache_root, "libplacebo")
                    with BUNDLED_ENGINE_LOCK:
                        copied_tools = [os.path.join(extracted, name) for name in ffmpeg_tool_names()]
                        if not all(os.path.isfile(tool) for tool in copied_tools):
                            staging = f"{cache_root}.copy-{uuid.uuid4().hex}"
                            try:
                                shutil.copytree(candidate, os.path.join(staging, "libplacebo"))
                                os.makedirs(os.path.dirname(cache_root), exist_ok=True)
                                os.rename(staging, cache_root)
                            except Exception:
                                shutil.rmtree(staging, ignore_errors=True)
                                raise
                    return extracted
                except OSError:
                    return None
            return candidate
    return bundled_ffmpeg_dir()


def ensure_bundled_ffmpeg(engine=None):
    d = bundled_engine_dir(engine)
    if not d:
        return False
    remove_macos_quarantine(d)
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    lib_dir = os.path.join(d, "lib")
    # The packaged libplacebo binary already has @loader_path rpaths. Setting
    # DYLD_LIBRARY_PATH makes macOS validate the large dependency set through
    # its slow fallback search path; use the embedded rpaths instead.
    if os.path.isdir(lib_dir) and getattr(engine, "engine_id", "") != "libplacebo-deband-v1":
        os.environ["DYLD_LIBRARY_PATH"] = lib_dir + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")
    if getattr(engine, "engine_id", "") == "libplacebo-deband-v1":
        runtime_env = LIBPLACEBO_ENGINE.runtime_environment(os.path.join(d, "ffmpeg"), os.environ)
        os.environ.pop("DYLD_LIBRARY_PATH", None)
        if runtime_env.get("VK_ICD_FILENAMES"):
            os.environ["VK_ICD_FILENAMES"] = runtime_env["VK_ICD_FILENAMES"]
    else:
        icd = os.path.join(d, "vulkan", "icd.d", "MoltenVK_icd.json")
        if os.path.isfile(icd):
            os.environ["VK_ICD_FILENAMES"] = icd
    return True


def show_missing_dependency_error():
    """Make an incomplete frozen app understandable when it has no console."""
    message = "The app's bundled FFmpeg tools are missing. Please download a fresh copy of 10-bit Converter."
    print("ERROR:", message)
    show_error("10-bit Converter", message)


def ensure_app_support_dir():
    """Create the user-writable state directory, never the signed app bundle."""
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)


def atomic_json_write(path, data):
    """Avoid corrupting preferences if the app is interrupted while saving."""
    ensure_app_support_dir()
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


UPDATE_CHECK_INTERVAL_SECONDS = 7 * 24 * 60 * 60
UPDATE_STATE_LOCK = threading.Lock()
UPDATE_REQUEST_LOCK = threading.Lock()
UPDATE_STATE = {"last_checked_at": 0, "last_notice_check_at": 0, "result": None, "checking": False}


def load_update_state():
    """Restore only the small, non-identifying weekly update-check record."""
    try:
        with open(UPDATE_STATE_PATH, encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            UPDATE_STATE["last_checked_at"] = float(saved.get("last_checked_at", 0) or 0)
            UPDATE_STATE["last_notice_check_at"] = float(saved.get("last_notice_check_at", 0) or 0)
            result = saved.get("result")
            if isinstance(result, dict):
                UPDATE_STATE["result"] = result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


def persist_update_state():
    with UPDATE_STATE_LOCK:
        snapshot = {
            "last_checked_at": UPDATE_STATE["last_checked_at"],
            "last_notice_check_at": UPDATE_STATE["last_notice_check_at"],
            "result": UPDATE_STATE["result"],
        }
    try:
        atomic_json_write(UPDATE_STATE_PATH, snapshot)
    except OSError:
        pass


def run_update_check():
    """Check the public release feed once; calls are serialized and bounded."""
    with UPDATE_REQUEST_LOCK:
        with UPDATE_STATE_LOCK:
            UPDATE_STATE["checking"] = True
        result = check_for_update(APP_VERSION)
        with UPDATE_STATE_LOCK:
            UPDATE_STATE["checking"] = False
            UPDATE_STATE["last_checked_at"] = time.time()
            UPDATE_STATE["result"] = result
        persist_update_state()
        return result


def check_weekly_update_if_due():
    """Run in the background at app start only when the seven-day window is due."""
    with UPDATE_STATE_LOCK:
        due = (time.time() - UPDATE_STATE["last_checked_at"]) >= UPDATE_CHECK_INTERVAL_SECONDS
    if due:
        run_update_check()


def update_status():
    with UPDATE_STATE_LOCK:
        result = dict(UPDATE_STATE["result"] or {})
        checking = bool(UPDATE_STATE["checking"])
    return {"checking": checking, "result": result}


def claim_weekly_update_notice():
    """Return a newer release at most once for each weekly check result."""
    with UPDATE_STATE_LOCK:
        result = dict(UPDATE_STATE["result"] or {})
        checking = bool(UPDATE_STATE["checking"])
        checked_at = UPDATE_STATE["last_checked_at"]
        should_notify = bool(
            result.get("ok") and result.get("update_available")
            and checked_at > UPDATE_STATE["last_notice_check_at"]
        )
        if should_notify:
            UPDATE_STATE["last_notice_check_at"] = checked_at
    if should_notify:
        persist_update_state()
    return {"checking": checking, "notice": result if should_notify else None}


def load_window_state():
    """Load a conservative native-window placement without trusting stale data."""
    state = {"width": 1180, "height": 820, "x": None, "y": None}
    try:
        with open(WINDOW_STATE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state["width"] = max(820, min(4096, int(saved.get("width", state["width"]))))
            state["height"] = max(620, min(3000, int(saved.get("height", state["height"]))))
            for axis in ("x", "y"):
                value = saved.get(axis)
                if isinstance(value, (int, float)) and -10000 <= value <= 10000:
                    state[axis] = int(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return state


WINDOW_STATE = {"width": 1180, "height": 820, "x": None, "y": None}
WINDOW_STATE_LOCK = threading.Lock()
WINDOW_STATE_TIMER = None


def save_window_state():
    """Persist the latest native window state atomically, outside the app bundle."""
    global WINDOW_STATE_TIMER
    with WINDOW_STATE_LOCK:
        snapshot = dict(WINDOW_STATE)
        WINDOW_STATE_TIMER = None
    try:
        atomic_json_write(WINDOW_STATE_PATH, snapshot)
    except OSError:
        pass


def schedule_window_state_save(*_):
    """Debounce Cocoa move/resize events so dragging a window does not churn disk."""
    global WINDOW_STATE_TIMER
    with WINDOW_STATE_LOCK:
        if WINDOW_STATE_TIMER:
            WINDOW_STATE_TIMER.cancel()
        WINDOW_STATE_TIMER = threading.Timer(0.5, save_window_state)
        WINDOW_STATE_TIMER.daemon = True
        WINDOW_STATE_TIMER.start()


def remember_window_size(width, height, *_):
    with WINDOW_STATE_LOCK:
        WINDOW_STATE["width"] = max(820, min(4096, int(width)))
        WINDOW_STATE["height"] = max(620, min(3000, int(height)))
    schedule_window_state_save()


def remember_window_position(x, y, *_):
    with WINDOW_STATE_LOCK:
        WINDOW_STATE["x"] = max(-10000, min(10000, int(x)))
        WINDOW_STATE["y"] = max(-10000, min(10000, int(y)))
    schedule_window_state_save()


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
    if clean["on_exists"] not in {"skip", "overwrite", "rename"}:
        clean["on_exists"] = DEFAULT_SETTINGS["on_exists"]
    clean["colour_safe"] = bool(clean["colour_safe"])
    if clean["source_interpretation"] not in {"preserve", "rec709_limited", "srgb_full"}:
        clean["source_interpretation"] = "preserve"
    try:
        atomic_json_write(SETTINGS_PATH, clean)
    except Exception:
        pass
    return clean


CUSTOM_PRESETS_PATH = os.path.join(APP_SUPPORT_DIR, "custom_presets.json")
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
        atomic_json_write(CUSTOM_PRESETS_PATH, presets)
    except Exception:
        pass


def deband_noise_chain(thr, rng=16, blur=True, dither=2, deflicker=False, max_quality=False,
                       denoise="off", colour_safe=False):
    return DEFAULT_ENGINE.build_filter_chain(
        thr, "yuv420p10le", range=rng, blur=blur, dither=dither,
        deflicker=deflicker, max_quality=max_quality, denoise=denoise, colour_safe=colour_safe,
    ).rsplit(",format=yuv420p10le", 1)[0]


def build_filters(thr, pix_fmt, rng=16, blur=True, dither=2, deflicker=False, max_quality=False,
                  denoise="off", colour_safe=False):
    return DEFAULT_ENGINE.build_filter_chain(
        thr, pix_fmt, range=rng, blur=blur, dither=dither,
        deflicker=deflicker, max_quality=max_quality, denoise=denoise, colour_safe=colour_safe,
    )


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
        scores.append((t, _column_banding_score(r.stdout, w, h)))
    if not scores:
        return {"score": 0, "band": "unknown", "message": "Couldn't analyze this file.", "samples": 0}
    score = sum(s for _, s in scores) / len(scores)
    worst_time, worst_score = max(scores, key=lambda pair: pair[1])
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
    recommended = "Low" if score < 10 else "Medium" if score < 35 else "High"
    return {"score": round(score, 1), "band": band, "message": message, "samples": len(scores),
            "worst_time": round(worst_time, 2), "worst_score": round(worst_score, 1),
            "recommended_strength": recommended}


def verify_output(path):
    pf = probe_pix_fmt(path)
    bits = pixfmt_bits(pf)
    kbps = probe_bitrate_kbps(path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    check = "✓" if bits >= 10 else "⚠"
    return f"{check} {bits}-bit ({pf}) · {kbps / 1000:.1f} Mbps · {human_size(size)}"


AUTHORIZED_PATHS = set()
AUTHORIZED_PATHS_LOCK = threading.Lock()


def _canonical_path(path):
    return os.path.realpath(os.path.abspath(path)) if path else ""


def authorize_path(path):
    """Remember files deliberately selected through this app for API actions."""
    path = _canonical_path(path)
    if path:
        with AUTHORIZED_PATHS_LOCK:
            AUTHORIZED_PATHS.add(path)
    return path


def is_authorized_path(path):
    path = _canonical_path(path)
    with AUTHORIZED_PATHS_LOCK:
        return bool(path) and path in AUTHORIZED_PATHS


def item_for(path):
    path = authorize_path(path)
    return {"path": path, "name": os.path.basename(path), **probe_info(path)}


def queue_stub(path):
    """Authorize a selected file without blocking the UI on ffprobe."""
    path = authorize_path(path)
    return {"path": path, "name": os.path.basename(path)}


def mode_kind(mode):
    """Return the encoder family for a UI export mode (never trust UI text)."""
    value = str(mode or "")
    if value.startswith("ProRes"):
        return "prores"
    if value.startswith("H.264"):
        return "h264"
    return "hevc"


def normalise_job_params(item, default_mode, default_strength, default_rate):
    """Resolve an optional per-clip override against the batch defaults."""
    override = item.get("override") if isinstance(item.get("override"), dict) else {}
    mode = override.get("mode", default_mode)
    strength = override.get("strength", default_strength)
    rate = override.get("rate", default_rate)
    valid_modes = {"HEVC (smaller, delivery)", "H.264 (10-bit, delivery)",
                   "ProRes 4444 (grading, huge file)"}
    if mode not in valid_modes:
        mode = default_mode
    if strength not in STRENGTH_THR:
        strength = default_strength
    if rate not in {"Match source", "Quality (CRF)", "Custom"}:
        rate = default_rate
    return mode, strength, rate, override


def format_label(mode):
    return {"prores": "ProRes 4444", "h264": "H.264 10-bit", "hevc": "HEVC"}[mode_kind(mode)]


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


def stream_map_args():
    """Keep the edited primary video plus every audio stream and container
    metadata/chapters. Subtitle handling is intentionally conservative because
    bitmap subtitle codecs cannot safely be placed in every target container."""
    return ["-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0"]


def subtitle_stream_args(in_path):
    """Carry text subtitles into MP4/MOV as mov_text when safe.

    Bitmap subtitle tracks (PGS/DVD) cannot be represented reliably in these
    delivery containers; those are reported rather than silently causing the
    entire export to fail.
    """
    codecs = probe_subtitle_codecs(in_path)
    if not codecs:
        return [], ""
    text_codecs = {"mov_text", "subrip", "webvtt", "ass", "ssa"}
    if all(codec in text_codecs for codec in codecs):
        return ["-map", "0:s?", "-c:s", "mov_text"], ""
    return [], f"{len(codecs)} unsupported subtitle track(s) were not copied to this container"


def fmt_time(sec):
    if sec is None or sec < 0 or sec != sec:
        return "--:--"
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def osascript(script):
    if sys.platform != "darwin":
        return ""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _as_str(s):
    """Escape a string for embedding in an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify_completion(title, message):
    """Surface completion outside the app window where the OS supports it."""
    notify(title, message)


def native_file_dialog(kind, prompt, multiple=False):
    """Use a PyWebView-native picker when the desktop shell is available."""
    if not (NATIVE_WINDOW and NATIVE_WEBVIEW):
        return None
    try:
        dialog_type = (getattr(NATIVE_WEBVIEW, "FOLDER_DIALOG", None)
                       if kind == "folder" else getattr(NATIVE_WEBVIEW, "OPEN_DIALOG", None))
        result = NATIVE_WINDOW.create_file_dialog(
            dialog_type, allow_multiple=multiple, directory="", file_types=("Video files (*.mp4;*.mov;*.mkv;*.avi;*.m4v;*.webm;*.mpg;*.mpeg;*.ts)",)
        )
        return [str(p) for p in (result or [])]
    except Exception:
        return None


def pick_files():
    native = native_file_dialog("files", "Select video(s) to convert", multiple=True)
    if native is not None:
        return native
    fallback = fallback_file_dialog("files", "Select video(s) to convert", multiple=True)
    if fallback is not None:
        return fallback
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
    native = native_file_dialog("folder", "Select a folder of videos")
    if native is not None:
        p = native[0] if native else ""
        if not p or not os.path.isdir(p):
            return []
        return [os.path.join(p, f) for f in sorted(os.listdir(p))
                if os.path.isfile(os.path.join(p, f)) and f.lower().endswith(VIDEO_EXTS)
                and "_10bit." not in f]
    fallback = fallback_file_dialog("folder", "Select a folder of videos")
    if fallback is not None:
        p = fallback[0] if fallback else ""
        if not p or not os.path.isdir(p):
            return []
        return [os.path.join(p, f) for f in sorted(os.listdir(p))
                if os.path.isfile(os.path.join(p, f)) and f.lower().endswith(VIDEO_EXTS)
                and "_10bit." not in f]
    p = osascript('try\nreturn POSIX path of (choose folder with prompt '
                  '"Select a folder of videos")\nend try')
    if not p or not os.path.isdir(p):
        return []
    return [os.path.join(p, f) for f in sorted(os.listdir(p))
            if os.path.isfile(os.path.join(p, f)) and f.lower().endswith(VIDEO_EXTS)
            and "_10bit." not in f]


def pick_folder_path(prompt="Select a folder to watch"):
    """Just the folder path (no listing) — for Watch folder, not file pickers."""
    native = native_file_dialog("folder", prompt)
    if native is not None:
        p = native[0] if native else ""
        return authorize_path(p) if p and os.path.isdir(p) else ""
    fallback = fallback_file_dialog("folder", prompt)
    if fallback is not None:
        p = fallback[0] if fallback else ""
        return authorize_path(p) if p and os.path.isdir(p) else ""
    p = osascript(f'try\nreturn POSIX path of (choose folder with prompt "{prompt}")\nend try')
    return authorize_path(p) if p and os.path.isdir(p) else ""


# ------------------------------------------------------------------ conversion job
class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.running = False
        self.cancel = threading.Event()      # instant: kills the current ffmpeg process
        self.stop_after = threading.Event()  # graceful: finishes the current file, then halts
        self.items = []          # [{path,name,status,pct}]
        self.now = {"file": "—", "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--", "sec": 0}
        self.summary = None
        self.total = 0
        self.index = 0
        self.started = 0.0
        self.proc = None
        self.current_path = ""

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
RUNNING_PREVIEW = {"key": None, "jpeg": b""}


def running_preview_jpeg():
    """Small, cached source-frame preview for the active encode.

    It intentionally samples the source—not the partially-written output—so
    it remains reliable for every codec and never competes with the encoder's
    file writer. The browser only asks every five seconds.
    """
    with JOB.lock:
        if not JOB.running or not JOB.current_path or not os.path.isfile(JOB.current_path):
            return b""
        path = JOB.current_path
        seconds = max(0.0, float(JOB.now.get("sec", 0) or 0))
    key = (path, int(seconds // 2))
    if RUNNING_PREVIEW["key"] == key and RUNNING_PREVIEW["jpeg"]:
        return RUNNING_PREVIEW["jpeg"]
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", path, "-frames:v", "1",
        "-vf", "scale=480:320:force_original_aspect_ratio=decrease:flags=lanczos,"
               "pad=480:320:(ow-iw)/2:(oh-ih)/2:color=0x090b10",
        "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True)
    if result.returncode == 0 and result.stdout:
        RUNNING_PREVIEW.update(key=key, jpeg=result.stdout)
        return result.stdout
    return b""

LAST_REPORT = {}
REPORT_LOG_PATH = os.path.join(APP_SUPPORT_DIR, "conversion_log.jsonl")
FAILURE_LOG_DIR = os.path.join(APP_SUPPORT_DIR, "failure-logs")


def write_failure_log(name, command, error, partial_path=""):
    """Persist a concise local diagnostic for a failed FFmpeg invocation."""
    try:
        os.makedirs(FAILURE_LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:48] or "export"
        path = os.path.join(FAILURE_LOG_DIR, f"{stamp}-{safe_name}-{uuid.uuid4().hex[:8]}.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("10-bit Converter failure report\n")
            handle.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            handle.write(f"Source: {name}\n")
            handle.write(f"Partial export: {partial_path or 'none'}\n\n")
            handle.write("Command:\n" + " ".join(map(str, command or [])) + "\n\n")
            handle.write("FFmpeg error:\n" + (error or "No diagnostic output was returned.") + "\n")
        return authorize_path(path)
    except OSError:
        return ""


def mark_item_failed(index, name, error, command=None, partial_path=None):
    """Make failed work recoverable: retain a log, discard incomplete media."""
    partial_note = "No output was written."
    if partial_path and os.path.exists(partial_path):
        try:
            os.remove(partial_path)
            partial_note = "Incomplete export was discarded safely."
        except OSError:
            partial_note = f"Incomplete export remains at {partial_path}."
    log_path = write_failure_log(name, command or [], error, partial_path or "")
    _set_item(index, status="Failed", pct="—", error=error,
              recovery=partial_note, log_path=log_path)


def build_and_store_report(items, mode, strength, rate, settings, done, skipped, failed, cancelled, started,
                           engine=DEFAULT_ENGINE):
    """A human-readable summary of what a batch just did: what was converted,
    with what settings, and the size/duration outcome. Kept in memory for the
    UI and appended to a log file on disk for later troubleshooting/reference."""
    rows = []
    total_in = total_out = 0
    for it in items:
        in_size = 0
        try:
            in_size = os.path.getsize(it["path"]) if os.path.exists(it["path"]) else 0
        except OSError:
            pass
        out_size = 0
        out_path = it.get("out")
        if out_path and os.path.exists(out_path):
            try:
                out_size = os.path.getsize(out_path)
            except OSError:
                pass
        total_in += in_size
        total_out += out_size
        rows.append({
            "name": it["name"], "status": it.get("status"), "info": it.get("info", ""),
            "error": it.get("error", ""), "profile": it.get("profile", ""),
            "source": it.get("path", ""), "output": out_path or "",
            "log_path": it.get("log_path", ""), "recovery": it.get("recovery", ""),
            "output_suffix": it.get("output_suffix", ""),
            # Store a resolved profile so a history retry remains faithful even
            # after the user changes the current global profile.
            "override": it.get("override") or {
                "mode": mode, "strength": strength, "rate": rate,
                "target_mbps": settings.get("target_mbps", 12),
            },
            "in_size": human_size(in_size) if in_size else "",
            "out_size": human_size(out_size) if out_size else "",
        })
    report = {
        "id": uuid.uuid4().hex,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "",
        "mode": mode, "strength": strength, "rate": rate,
        "engine": engine.engine_id,
        "engine_name": engine.display_name,
        "settings": {k: settings.get(k) for k in DEFAULT_SETTINGS},
        "done": done, "skipped": skipped, "failed": failed, "cancelled": cancelled,
        "total_in_size": human_size(total_in) if total_in else "",
        "total_out_size": human_size(total_out) if total_out else "",
        "elapsed_sec": round(time.time() - started, 1) if started else 0,
        "items": rows,
    }
    LAST_REPORT.clear()
    LAST_REPORT.update(report)
    try:
        ensure_app_support_dir()
        append_report(REPORT_LOG_PATH, report)
    except Exception:
        pass
    return report


def load_history(limit=40):
    """Read recent durable reports and re-authorize only their existing paths."""
    return read_reports(REPORT_LOG_PATH, limit, authorize_path)


# Watch folder: polls a folder for new videos and auto-converts them using
# whatever mode/strength/rate the user last ran manually (updated in the
# /api/convert handler). Runs in its own daemon thread, started once in main().
WATCH = {"enabled": False, "folder": "", "processed": 0}
LAST_RUN = {"mode": "HEVC (smaller, delivery)", "strength": "Medium", "rate": "Match source"}
_watch_sizes = {}   # path -> last-seen size, for a simple stability check
WATCH_SERVICE = None
SHUTDOWN_EVENT = threading.Event()
NATIVE_WINDOW = None
NATIVE_WEBVIEW = None


def watch_tick():
    """One poll: queue+convert any new, size-stable video in the watched
    folder whose output doesn't already exist. No-ops if a batch is already
    running (manual or watch) or watching is off."""
    if WATCH_SERVICE is not None:
        return WATCH_SERVICE.tick()
    if not WATCH["enabled"] or not WATCH["folder"] or JOB.running:
        return
    folder = WATCH["folder"]
    if not os.path.isdir(folder):
        return
    settings = load_settings()
    is_prores = LAST_RUN["mode"].startswith("ProRes")
    dest_dir = settings["dest_dir"] if settings["dest_mode"] == "custom" else ""
    suffix = settings["suffix"] or "_10bit"
    ready = []
    seen_now = set()
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(VIDEO_EXTS) or "_10bit." in f or f"{suffix}." in f:
            continue
        full = os.path.join(folder, f)
        if not os.path.isfile(full):
            continue
        seen_now.add(full)
        out = make_output_path(full, is_prores, dest_dir, suffix)
        if os.path.exists(out):
            continue  # already converted — safe to re-run the app without reprocessing everything
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        # Require the same size across two consecutive polls before touching a
        # file — a drag-and-drop copy in progress would still be growing.
        if _watch_sizes.get(full) == size:
            ready.append(full)
        _watch_sizes[full] = size
    for stale in set(_watch_sizes) - seen_now:
        del _watch_sizes[stale]
    if not ready:
        return
    items = [{"path": authorize_path(p), "name": os.path.basename(p), "status": "Queued", "pct": ""}
             for p in ready]
    with JOB.lock:
        JOB.reset()
        JOB.running = True
        JOB.items = items
    WATCH["processed"] += len(items)
    run_batch(items, LAST_RUN["mode"], LAST_RUN["strength"], LAST_RUN["rate"], settings)


def watch_loop():
    while not SHUTDOWN_EVENT.is_set():
        try:
            watch_tick()
        except Exception:
            pass
        SHUTDOWN_EVENT.wait(3)


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


def estimate_export_bytes(item, mode, rate, settings):
    info = probe_info(item["path"])
    return estimate_export_bytes_pure(info, mode, rate, settings, mode_kind)


def build_preflight(items, mode, strength, rate, settings):
    dest_dir = settings["dest_dir"] if settings["dest_mode"] == "custom" else ""
    suffix = settings["suffix"] or "_10bit"
    rows, folders, total, reserved, folder_estimates = [], set(), 0, set(), {}
    on_exists = settings.get("on_exists", "skip")
    for it in items:
        item_mode, item_strength, item_rate, override = normalise_job_params(it, mode, strength, rate)
        local_settings = dict(settings)
        if "target_mbps" in override:
            try: local_settings["target_mbps"] = float(override["target_mbps"])
            except (TypeError, ValueError): pass
        base_out = make_output_path(it["path"], mode_kind(item_mode) == "prores", dest_dir,
                                    str(it.get("output_suffix") or suffix))
        collision = os.path.exists(base_out) or base_out in reserved
        out = resolve_output_path(base_out, on_exists, reserved)
        reserved.add(out)
        estimate = estimate_export_bytes(it, item_mode, item_rate, local_settings)
        total += estimate
        folder = os.path.dirname(out); folders.add(folder)
        folder_estimates[folder] = folder_estimates.get(folder, 0) + estimate
        rows.append({"name": it.get("name", os.path.basename(it["path"])), "out": out,
                     "format": format_label(item_mode), "strength": item_strength,
                     "exists": collision, "renamed": out != base_out, "estimate": estimate})
    disks, blocking, warnings = [], [], []
    for folder in sorted(folders):
        try:
            base, writable = writable_parent(folder)
            if not writable:
                blocking.append(f"Cannot write to {folder}.")
                continue
            free = shutil.disk_usage(base).free
            needed = folder_estimates.get(folder, 0)
            disks.append({"folder": folder, "free": free, "needed": needed, "writable": True})
            # Leave room for filesystem overhead and temporary files. Unknown
            # CRF sizes remain warnings rather than false hard blocks.
            if needed and needed * 1.1 > free:
                blocking.append(f"Not enough free space in {folder}: need about {human_size(int(needed * 1.1))}, have {human_size(free)}.")
            elif not needed:
                warnings.append(f"Could not estimate final size for files exporting to {folder}.")
        except OSError:
            blocking.append(f"Could not inspect the export destination: {folder}.")
    for it in items:
        if not os.path.isfile(it.get("path", "")):
            blocking.append(f"Source file is missing: {it.get('name', 'unknown file')}.")
    return {"items": rows, "total_estimate": total, "disks": disks,
            "collisions": [r["name"] for r in rows if r["exists"]],
            "on_exists": on_exists, "blocking": blocking, "warnings": warnings,
            "ready": not blocking}


def set_dock_badge(text):
    if not NATIVE_WINDOW:
        return
    try:
        from AppKit import NSApp
        NSApp.dockTile().setBadgeLabel_(text or None)
    except Exception:
        pass


def run_batch(items, mode, strength, rate, settings, engine=DEFAULT_ENGINE):
    if not ensure_bundled_ffmpeg(engine):
        with JOB.lock:
            JOB.summary = f"Could not start {engine.display_name}: bundled engine files are missing."
            JOB.running = False
        return
    dest_dir = settings["dest_dir"] if settings["dest_mode"] == "custom" else ""
    suffix = settings["suffix"] or "_10bit"
    on_exists = settings.get("on_exists", "skip")
    overwrite = on_exists == "overwrite"
    reserved_outputs = set()
    total = len(items)
    done = failed = skipped = 0
    last_output = None
    stopped_after_current = False
    keep_awake = KeepAwake()
    with JOB.lock:
        JOB.total = total
        JOB.started = time.time()
    try:
        # Keep the computer awake only while this user-initiated batch runs.
        # The adapter uses caffeinate on macOS and SetThreadExecutionState on
        # Windows; neither persists or changes the user's power settings.
        keep_awake.start()
    except Exception:
        pass
    set_dock_badge("0%")

    try:
        for idx, it in enumerate(items, start=1):
            if JOB.cancel.is_set():
                break
            if JOB.stop_after.is_set():
                # Graceful stop: the previous file (if any) already finished
                # normally; don't start another one. Mark the rest Skipped so
                # the queue reflects what actually happened.
                stopped_after_current = True
                remaining = len(items) - (idx - 1)
                skipped += remaining
                for j in range(idx - 1, len(items)):
                    _set_item(j, status="Skipped", pct="—")
                break
            temp_out_path = None
            cmd = None
            try:
                with JOB.lock:
                    JOB.index = idx
                in_path = it["path"]
                plan = CONVERSION_PLANNER.plan(it, mode, strength, rate, settings, engine)
                item_mode, item_strength, item_rate = plan["mode"], plan["strength"], plan["rate"]
                override, item_settings = plan["override"], plan["settings"]
                kind, is_prores, filters = plan["kind"], plan["is_prores"], plan["filters"]
                item_suffix = str(it.get("output_suffix") or suffix)
                base_out_path = CONVERSION_PLANNER.output_path(in_path, is_prores, dest_dir, item_suffix)
                out_path = resolve_output_path(base_out_path, on_exists, reserved_outputs)
                reserved_outputs.add(out_path)
                if dest_dir and not os.path.isdir(dest_dir):
                    try:
                        os.makedirs(dest_dir, exist_ok=True)
                    except OSError:
                        pass
                if os.path.exists(out_path) and not overwrite:
                    skipped += 1
                    _set_item(idx - 1, status="Skipped", pct="—")
                    continue
                temp_out_path = staging_output_path(out_path)
                aud, col, streams, provenance = (plan["audio"], plan["colour"], plan["streams"],
                                                   plan["provenance"])
                subtitle_note = plan["subtitle_note"]
                pre_cmd = None
                if is_prores:
                    cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, *streams, "-vf", filters,
                           "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                           *(["-c:a", "pcm_s16le"] if aud == ["-an"] else aud), *col, *provenance,
                           "-progress", "pipe:1", temp_out_path]
                else:
                    rate_args = plan["rate_args"]
                    encoder = "libx264" if kind == "h264" else "libx265"
                    base = ["ffmpeg", "-y", "-nostats", "-i", in_path, *streams, "-vf", filters,
                            "-c:v", encoder, "-pix_fmt", "yuv420p10le", *rate_args,
                            "-preset", str(item_settings["preset"])]
                    # Two-pass only makes sense with a bitrate target (not CRF).
                    two_pass = bool(item_settings.get("two_pass")) and "-b:v" in rate_args
                    if two_pass:
                        stats = os.path.join(INTAKE_DIR, f"2pass_{idx}.log")
                        pass_param = "-x264-params" if kind == "h264" else "-x265-params"
                        # Pass one analyzes video only; carrying audio or text
                        # subtitle streams into the null muxer is unnecessary
                        # and can fail for otherwise valid source files.
                        pre_base = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-map", "0:v:0", "-vf", filters,
                                    "-c:v", encoder, "-pix_fmt", "yuv420p10le", *rate_args,
                                    "-preset", str(item_settings["preset"])]
                        colour_value = plan.get("colour_encoder", ["", ""])[1]
                        pass_one = f"pass=1:stats={stats}" + (f":{colour_value}" if colour_value else "")
                        pre_cmd = [*pre_base, pass_param, pass_one, "-an",
                                   "-f", "null", "-progress", "pipe:1", os.devnull]
                        pass_two = f"pass=2:stats={stats}" + (f":{colour_value}" if colour_value else "")
                        codec_args = [pass_param, pass_two]
                        if kind == "hevc":
                            codec_args += ["-tag:v", "hvc1"]
                        cmd = [*base, *codec_args, *aud, *col, *provenance, "-movflags", "+faststart",
                               "-progress", "pipe:1", temp_out_path]
                    else:
                        codec_args = [*plan.get("colour_encoder", []), *( ["-tag:v", "hvc1"] if kind == "hevc" else [])]
                        cmd = [*base, *codec_args, *aud, *col, *provenance, "-movflags", "+faststart",
                               "-progress", "pipe:1", temp_out_path]
                src_bits = pixfmt_bits(probe_pix_fmt(in_path))
                dur = probe_duration(in_path)
                name = it["name"]
                profile = f"{format_label(item_mode)} · {item_strength} deband"
                profile += " · codec-managed" if is_prores else f" · {item_rate}"
                with JOB.lock:
                    JOB.current_path = in_path
                _set_item(idx - 1, status="Running", pct="0%", profile=profile)

                # Pass 1 (two-pass only): analysis pass, discarded output.
                if pre_cmd is not None:
                    with JOB.lock:
                        JOB.now = {"file": f"[{idx}/{total}]  {name} — pass 1/2 (analyzing)",
                                   "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--", "sec": 0}
                    e1 = _run_ffmpeg(pre_cmd, dur, idx - 1)
                    if not JOB.cancel.is_set() and e1 is not None:
                        failed += 1
                        mark_item_failed(idx - 1, name, e1, pre_cmd, temp_out_path)
                        continue

                err = None
                if not JOB.cancel.is_set():
                    with JOB.lock:
                        JOB.now = {"file": f"[{idx}/{total}]  {name}" + (" — pass 2/2" if pre_cmd else ""),
                                   "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--", "sec": 0}
                    err = _run_ffmpeg(cmd, dur, idx - 1)
                if JOB.cancel.is_set():
                    try:
                        if os.path.exists(temp_out_path):
                            os.remove(temp_out_path)
                    except OSError:
                        pass
                    _set_item(idx - 1, status="Cancelled", pct="—")
                    break
                if err is not None:
                    failed += 1
                    mark_item_failed(idx - 1, name, err, cmd, temp_out_path)
                    continue
                if pixfmt_bits(probe_pix_fmt(temp_out_path)) < 10:
                    failed += 1
                    mark_item_failed(idx - 1, name,
                                     "Output verification failed: not a 10-bit video stream.",
                                     cmd, temp_out_path)
                    continue
                os.replace(temp_out_path, out_path)
                done += 1
                last_output = out_path
                authorize_path(out_path)
                info = verify_output(out_path)
                if subtitle_note:
                    info += f" · {subtitle_note}"
                if src_bits >= 10:
                    info = f"source was {src_bits}-bit (deband only) · " + info

                # Dual export: alongside the ProRes master, also cut a small
                # HEVC preview from the same filter chain — useful for quick
                # review/sharing without opening the (huge) grading master.
                if is_prores and item_settings.get("dual_export") and not JOB.cancel.is_set():
                    preview_path = make_output_path(in_path, False, dest_dir, item_suffix + "_preview")
                    preview_path = resolve_output_path(preview_path, on_exists, reserved_outputs)
                    reserved_outputs.add(preview_path)
                    if not (os.path.exists(preview_path) and not overwrite):
                        temp_preview_path = staging_output_path(preview_path)
                        with JOB.lock:
                            JOB.now = {"file": f"[{idx}/{total}]  {name} — HEVC preview",
                                       "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--", "sec": 0}
                        pv_aud = audio_args(in_path, False, item_settings.get("audio", "copy"))
                        pv_cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, *streams, "-vf", filters,
                                  "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-crf", "20",
                                  "-preset", "veryfast", "-tag:v", "hvc1", *pv_aud, *col,
                                  "-progress", "pipe:1", temp_preview_path]
                        # item_idx=-1: the preview isn't tracked as its own row,
                        # so route progress updates only to JOB.now, not a row.
                        pv_err = _run_ffmpeg(pv_cmd, dur, -1)
                        if pv_err is None and os.path.isfile(temp_preview_path):
                            os.replace(temp_preview_path, preview_path)
                            authorize_path(preview_path)
                            info += f" · preview: {os.path.basename(preview_path)} ({verify_output(preview_path)})"
                        else:
                            try: os.remove(temp_preview_path)
                            except OSError: pass
                            info += " · preview export failed"

                _set_item(idx - 1, status="Done", pct="100%", info=info, out=out_path)
            except Exception as e:
                # A crash in one item must not hang the batch forever (JOB.running
                # would otherwise never be reset) or take down the worker thread
                # silently — mark this item Failed and move on to the next.
                failed += 1
                mark_item_failed(idx - 1, it.get("name", "export"),
                                 f"Unexpected error: {e}", locals().get("cmd"), temp_out_path)
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
        elif stopped_after_current:
            parts.append("stopped after current file")
        summary = "Finished: " + ", ".join(parts) + "."
        started_at = JOB.started
        with JOB.lock:
            JOB.running = False
            JOB.summary = summary
            JOB.now = {"file": "—", "pct": 0, "frame": "", "fps": "", "speed": "", "eta": "--:--", "sec": 0}
            JOB.current_path = ""
        keep_awake.stop()
        set_dock_badge(None)
        if items:
            build_and_store_report(items, mode, strength, rate, settings,
                                   done, skipped, failed, cancelled, started_at, engine)
        if last_output and not cancelled:
            reveal_files([last_output])
        if items and not cancelled:
            noun = "file" if len(items) == 1 else "files"
            notify_completion("8-bit → 10-bit Converter", f"{summary} ({len(items)} {noun} in batch)")


WATCH_SERVICE = WatchService(JOB, WATCH, LAST_RUN, VIDEO_EXTS, authorize_path,
                             load_settings, make_output_path, run_batch)


def _set_item(i, **kw):
    with JOB.lock:
        if 0 <= i < len(JOB.items):
            JOB.items[i].update(kw)


def _run_ffmpeg(cmd, dur, item_idx):
    def on_process(proc):
        with JOB.lock:
            JOB.proc = proc

    def on_progress(cur):
        _emit(cur, dur, item_idx)

    try:
        return run_ffmpeg(cmd, JOB.cancel, on_progress=on_progress, on_process=on_process,
                          inactivity_timeout=120)
    finally:
        with JOB.lock:
            if getattr(JOB, "proc", None) is not None and JOB.proc.poll() is not None:
                JOB.proc = None


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
                   "speed": speed, "eta": fmt_time(eta), "sec": round(secs, 3)}
        if JOB.total:
            overall = ((JOB.index - 1) + pct / 100) / JOB.total * 100
            if int(overall) % 5 == 0:
                set_dock_badge(f"{int(overall)}%")
        if item_idx is not None and 0 <= item_idx < len(JOB.items):
            JOB.items[item_idx]["pct"] = f"{pct:.0f}%"


# ------------------------------------------------------------------ previews
# PreviewService owns short-lived scope/compare/filmstrip artifacts.  The
# server only validates requests and serves the already-rendered image bytes.
PREVIEWS = PreviewService(INTAKE_DIR, authorize_path, DEFAULT_ENGINE,
                          deband_noise_chain, build_filters)
SCOPES = {}   # Legacy aliases retained until the next source-only cleanup.
FILMSTRIPS = {}


def _png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def write_rgb_png(path, width, height, pixels):
    """Write an RGB PNG without adding a runtime dependency to the app."""
    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    payload = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
               + _png_chunk(b"IDAT", zlib.compress(rows, 6)) + _png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(payload)


def render_histogram(rgb, sample_width, sample_height, out_path):
    """Draw a clean, high-resolution RGB histogram from the actual scope frame.

    FFmpeg's histogram filter is useful but its panel layout varies by build and
    was producing low-detail blocks. This is deliberately a Resolve-style
    three-channel readout: log-scaled data, consistent grid, and no resampling.
    """
    width, height = 1600, 900
    pixels = bytearray(width * height * 3)
    for i in range(0, len(pixels), 3):
        pixels[i:i + 3] = b"\x05\x07\x0a"

    def px(x, y, colour):
        if 0 <= x < width and 0 <= y < height:
            off = (y * width + x) * 3
            pixels[off:off + 3] = bytes(colour)

    left, right, top, bottom = 52, 18, 20, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    panel_h = plot_h // 3
    grid = (78, 62, 10)
    colours = ((255, 72, 72), (70, 230, 112), (80, 145, 255))
    bins = [[0] * 256 for _ in range(3)]
    for i in range(0, min(len(rgb), sample_width * sample_height * 3), 3):
        bins[0][rgb[i]] += 1
        bins[1][rgb[i + 1]] += 1
        bins[2][rgb[i + 2]] += 1
    for channel in range(3):
        y0 = top + channel * panel_h
        y1 = top + (channel + 1) * panel_h - 8
        for level in range(5):
            y = y0 + round((y1 - y0) * level / 4)
            for x in range(left, width - right):
                px(x, y, grid)
        for level in range(9):
            x = left + round(plot_w * level / 8)
            for y in range(y0, y1 + 1):
                px(x, y, grid)
        peak = max(bins[channel]) or 1
        peak_log = math.log1p(peak)
        dim = tuple(max(1, c // 4) for c in colours[channel])
        for level, count in enumerate(bins[channel]):
            x = left + round(level * plot_w / 255)
            value = math.log1p(count) / peak_log
            y = y1 - round(value * (y1 - y0 - 8))
            for fy in range(y, y1 + 1):
                px(x, fy, dim)
            for yy in range(max(y0, y - 1), min(y1 + 1, y + 2)):
                px(x, yy, colours[channel])
    write_rgb_png(out_path, width, height, pixels)


def scope_rgb_frame(in_path, t, vf, source_info):
    """Extract a deterministic RGB sample for the custom histogram."""
    src_w, src_h = source_info.get("width") or 1920, source_info.get("height") or 1080
    sample_w = max(2, min(960, src_w) // 2 * 2)
    sample_h = max(2, round(src_h * sample_w / src_w) // 2 * 2)
    filters = ",".join(part for part in (vf, f"scale={sample_w}:{sample_h}:flags=lanczos", "format=rgb24") if part)
    result = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", in_path, "-frames:v", "1",
                             "-vf", filters, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            capture_output=True)
    expected = sample_w * sample_h * 3
    if result.returncode != 0 or len(result.stdout) != expected:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "could not read frame pixels")
    return result.stdout, sample_w, sample_h


def render_scopes(in_path, strength, settings, t=None):
    thr = DEFAULT_ENGINE.threshold_for(strength, settings.get("thr_custom", "0.03"))
    chain = deband_noise_chain(thr, settings["deband_range"], settings["deband_blur"],
                               settings["dither"], settings.get("deflicker", False),
                               settings.get("max_quality", False), settings.get("denoise", "off"),
                               settings.get("colour_safe", False))
    outdir = tempfile.mkdtemp(prefix="scopes_")
    dur = probe_duration(in_path)
    if t is None:
        t = max(0.0, dur * 0.4) if dur else 1.0
    t = max(0.0, min(float(t), max(0.0, dur - 0.05))) if dur else float(t)
    # A 1280px source sample feeds 1600px presentation scopes. This keeps the
    # reading detailed on a Retina panel without turning a single preview into
    # a full-resolution re-encode. The scope filters are genuine waveform/
    # parade/vectorscope analysis of that selected source or processed frame.
    sample = "scale=w='min(1280,iw)':h=-2:flags=lanczos"
    source_prefix = sample
    after_prefix = f"{chain},{sample}"
    jobs = {}
    for prefix, filters in (("src", source_prefix), ("aft", after_prefix)):
        jobs[f"{prefix}_waveform"] = (
            f"{filters},format=yuv444p,waveform=mode=column:display=overlay:components=1:"
            "filter=lowpass:scale=ire:graticule=orange:opacity=0.8:intensity=0.15:mirror=0,"
            "scale=1600:900:flags=lanczos")
        jobs[f"{prefix}_parade"] = (
            f"{filters},format=gbrp,waveform=mode=column:display=parade:components=7:"
            "filter=color:scale=ire:graticule=orange:opacity=0.8:intensity=0.15:mirror=0,"
            "scale=1600:900:flags=lanczos")
        jobs[f"{prefix}_vectorscope"] = (
            f"{filters},format=yuv444p,vectorscope=mode=color3:colorspace=709:intensity=0.18:"
            "graticule=color:opacity=0.8,scale=1200:1200:flags=lanczos")
    errors = {}
    for which, vf in jobs.items():
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", in_path,
                           "-frames:v", "1", "-vf", vf, os.path.join(outdir, which + ".png")],
                          capture_output=True, text=True)
        out_file = os.path.join(outdir, which + ".png")
        if r.returncode != 0 or not os.path.isfile(out_file):
            errors[which] = r.stderr.strip() or f"ffmpeg exited {r.returncode}"
    source_info = probe_info(in_path)
    for prefix, filters in (("src", source_prefix), ("aft", after_prefix)):
        try:
            rgb, width, height = scope_rgb_frame(in_path, t, filters, source_info)
            render_histogram(rgb, width, height, os.path.join(outdir, f"{prefix}_histogram.png"))
        except Exception as e:
            errors[f"{prefix}_histogram"] = str(e)
    token = os.path.basename(outdir)
    SCOPES[token] = outdir
    return token, errors, t, dur


def render_processed_sample(in_path, strength, settings, t=None):
    """Render a short, representative HEVC Main10 sample for the inspector.

    This is deliberately user-triggered: it never competes with a full batch
    encode, and it uses the same deband/dither chain as conversion.
    """
    dur = probe_duration(in_path)
    length = min(3.0, dur) if dur else 3.0
    if t is None:
        t = dur * 0.4 if dur else 0.0
    t = max(0.0, min(float(t), max(0.0, dur - length))) if dur else max(0.0, float(t))
    thr = DEFAULT_ENGINE.threshold_for(strength, settings.get("thr_custom", "0.03"))
    filters = build_filters(thr, "yuv420p10le", settings["deband_range"],
                            settings["deband_blur"], settings["dither"],
                            settings.get("deflicker", False),
                            settings.get("max_quality", False), settings.get("denoise", "off"),
                            settings.get("colour_safe", False))
    out_path = os.path.join(INTAKE_DIR, f"processed_sample_{uuid.uuid4().hex}.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", in_path,
           "-t", f"{length:.3f}", "-map", "0:v:0", "-an", "-vf", filters,
           "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "veryfast",
           "-crf", "18", "-tag:v", "hvc1", "-movflags", "+faststart", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode or not os.path.isfile(out_path):
        raise RuntimeError(result.stderr.strip() or "FFmpeg could not render the sample")
    return authorize_path(out_path), round(t, 2), round(length, 2)


def render_filmstrip(in_path, count=8):
    """N evenly-spaced frames across the clip so the user can click to
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

    def _expected_origin(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def _request_is_trusted(self, parsed):
        """Keep an unguessable local session separate from arbitrary localhost tabs."""
        expected = self._expected_origin()
        host = self.headers.get("Host", "")
        if host != expected.removeprefix("http://"):
            self._send(403, {"error": "invalid host"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != expected:
            self._send(403, {"error": "invalid origin"})
            return False
        if parsed.path.startswith("/api/"):
            query_token = urllib.parse.parse_qs(parsed.query).get("auth", [""])[0]
            request_token = self.headers.get("X-10bit-Token", "")
            if not secrets.compare_digest(request_token or query_token, API_TOKEN):
                self._send(403, {"error": "invalid session"})
                return False
        return True

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400" if ctype == "image/jpeg" else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        if n > 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def _serve_media(self, path):
        """Range-aware local media serving so the native WebKit player can seek."""
        try:
            size = os.path.getsize(path)
            start, end = 0, size - 1
            raw_range = self.headers.get("Range", "")
            if raw_range.startswith("bytes="):
                left, _, right = raw_range[6:].partition("-")
                start = int(left) if left else 0
                end = int(right) if right else end
                start, end = max(0, start), min(size - 1, end)
            length = end - start + 1
            ext = os.path.splitext(path)[1].lower()
            ctype = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}.get(ext, "video/mp4")
            self.send_response(206 if raw_range else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if raw_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    data = f.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (OSError, ValueError, BrokenPipeError):
            pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if not self._request_is_trusted(u):
            return
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    page = f.read().decode("utf-8")
                bootstrap = (
                    f'const API_TOKEN = {json.dumps(API_TOKEN)}; '
                    f'const NATIVE_SHELL = {json.dumps(bool(NATIVE_WINDOW))};'
                )
                page = page.replace('const API = "";', f'const API = ""; {bootstrap}', 1)
                self._send(200, page, "text/html; charset=utf-8")
            except OSError:
                self._send(500, "index.html missing", "text/plain")
        elif u.path == "/JZB.png":
            try:
                with open(os.path.join(HERE, "JZB.png"), "rb") as f:
                    self._send(200, f.read(), "image/png")
            except OSError:
                self._send(404, "app icon missing", "text/plain")
        elif u.path.startswith("/ui/"):
            # Focused browser modules are static, same-origin assets. Keep the
            # route constrained to toolkit/ui so a malformed URL cannot read a
            # neighboring file from the app bundle.
            rel = urllib.parse.unquote(u.path[len("/ui/"):])
            root = os.path.realpath(os.path.join(HERE, "ui"))
            fp = os.path.realpath(os.path.join(root, rel))
            if not fp.startswith(root + os.sep) or not fp.endswith(".js"):
                self._send(404, "not found", "text/plain")
            else:
                try:
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), "application/javascript; charset=utf-8")
                except OSError:
                    self._send(404, "not found", "text/plain")
        elif u.path == "/api/status":
            self._send(200, JOB.snapshot())
        elif u.path == "/api/engines":
            ensure_bundled_ffmpeg()
            ffmpeg_name = ffmpeg_tool_names()[0]
            default_ffmpeg = os.path.join(bundled_ffmpeg_dir() or "", ffmpeg_name) or (shutil.which("ffmpeg") or "ffmpeg")
            optional_dir = bundled_engine_dir(LIBPLACEBO_ENGINE)
            optional_ffmpeg = os.path.join(optional_dir, ffmpeg_name) if optional_dir else default_ffmpeg
            self._send(200, {"engines": engine_catalog(default_ffmpeg, optional_ffmpeg)})
        elif u.path == "/api/running-preview":
            jpeg = running_preview_jpeg()
            if jpeg:
                self._send(200, jpeg, "image/jpeg")
            else:
                self._send(404, "no active preview", "text/plain")
        elif u.path == "/api/media":
            path = urllib.parse.parse_qs(u.query).get("path", [""])[0]
            if path and is_authorized_path(path) and os.path.isfile(path):
                self._serve_media(path)
            else:
                self._send(404, "media unavailable", "text/plain")
        elif u.path == "/api/settings":
            self._send(200, load_settings())
        elif u.path == "/api/presets":
            self._send(200, load_custom_presets())
        elif u.path == "/api/watch":
            self._send(200, dict(WATCH))
        elif u.path == "/api/report":
            self._send(200, dict(LAST_REPORT))
        elif u.path == "/api/history":
            self._send(200, {"reports": load_history()})
        elif u.path == "/api/update-status":
            self._send(200, update_status())
        elif u.path == "/api/update-notice":
            self._send(200, claim_weekly_update_notice())
        elif u.path == "/api/update":
            # A visible, manual check. Startup checks are scheduled separately
            # and at most once per week.
            self._send(200, run_update_check())
        elif u.path in ("/api/scope", "/api/compare-image", "/api/filmstrip-image"):
            q = urllib.parse.parse_qs(u.query)
            token = q.get("token", [""])[0]
            which = q.get("which", [""])[0]
            kind = {"/api/scope": "scope", "/api/compare-image": "compare",
                    "/api/filmstrip-image": "filmstrip"}[u.path]
            fp = PREVIEWS.image_path(kind, token, which)
            if fp and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, "not found", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._request_is_trusted(u):
            return
        if u.path == "/api/pick-files":
            paths = pick_files()
            self._send(200, [queue_stub(p) for p in paths if p.lower().endswith(VIDEO_EXTS)])
        elif u.path == "/api/pick-folder":
            paths = pick_folder()
            self._send(200, [queue_stub(p) for p in paths])
        elif u.path == "/api/pick-folder-path":
            self._send(200, {"folder": pick_folder_path("Select an output folder")})
        elif u.path == "/api/add-native-path":
            path = self._read_json().get("path", "")
            if not NATIVE_WINDOW or not path.lower().endswith(VIDEO_EXTS) or not os.path.isfile(path):
                self._send(400, {"error": "invalid native drop"})
                return
            self._send(200, queue_stub(path))
        elif u.path == "/api/probe":
            path = self._read_json().get("path", "")
            if not path or not is_authorized_path(path) or not os.path.isfile(path):
                self._send(404, {"error": "file not found"})
                return
            self._send(200, item_for(path))
        elif u.path == "/api/restore-queue":
            restored = []
            for it in self._read_json().get("items", []):
                path = it.get("path", "")
                if not path.lower().endswith(VIDEO_EXTS) or not os.path.isfile(path):
                    continue
                item = item_for(path)
                if isinstance(it.get("override"), dict):
                    item["override"] = it["override"]
                if isinstance(it.get("output_suffix"), str) and len(it["output_suffix"]) <= 80:
                    item["output_suffix"] = it["output_suffix"]
                restored.append(item)
            self._send(200, {"items": restored})
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
        elif u.path == "/api/check-overwrites":
            data = self._read_json()
            settings = load_settings()
            dest_dir = settings["dest_dir"] if settings["dest_mode"] == "custom" else ""
            suffix = settings["suffix"] or "_10bit"
            existing = []
            for it in data.get("items", []):
                path = it.get("path")
                if not path or not is_authorized_path(path):
                    continue
                item_mode, _, _, _ = normalise_job_params(
                    it, data.get("mode", "HEVC (smaller, delivery)"),
                    data.get("strength", "Medium"), data.get("rate", "Match source"))
                is_prores = mode_kind(item_mode) == "prores"
                item_suffix = str(it.get("output_suffix") or suffix)
                out = make_output_path(path, is_prores, dest_dir, item_suffix)
                if os.path.exists(out):
                    existing.append(it.get("name", os.path.basename(path)))
            self._send(200, {"existing": existing})
        elif u.path == "/api/preflight":
            data = self._read_json()
            items = [it for it in data.get("items", []) if it.get("path") and is_authorized_path(it["path"])]
            if not items:
                self._send(400, {"error": "no valid files"})
                return
            self._send(200, build_preflight(items, data.get("mode", "HEVC (smaller, delivery)"),
                                             data.get("strength", "Medium"), data.get("rate", "Match source"),
                                             load_settings()))
        elif u.path == "/api/convert":
            data = self._read_json()
            ensure_bundled_ffmpeg()
            default_ffmpeg = os.path.join(bundled_ffmpeg_dir() or "", "ffmpeg") or (shutil.which("ffmpeg") or "ffmpeg")
            optional_dir = bundled_engine_dir(LIBPLACEBO_ENGINE)
            optional_ffmpeg = os.path.join(optional_dir, "ffmpeg") if optional_dir else default_ffmpeg
            requested, reason = requested_engine(data.get("engine"), default_ffmpeg, optional_ffmpeg)
            if requested is None:
                self._send(409, {"error": reason})
                return
            items = [{"path": it["path"], "name": it.get("name", os.path.basename(it["path"])),
                      "status": "Queued", "pct": "", "override": it.get("override")
                      if isinstance(it.get("override"), dict) else None,
                      "output_suffix": it.get("output_suffix") if isinstance(it.get("output_suffix"), str)
                      and len(it.get("output_suffix")) <= 80 else ""}
                     for it in data.get("items", [])
                     if it.get("path") and is_authorized_path(it["path"])]
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
            mode = data.get("mode", "HEVC"); strength = data.get("strength", "Medium")
            rate = data.get("rate", "Match source")
            LAST_RUN["mode"], LAST_RUN["strength"], LAST_RUN["rate"] = mode, strength, rate
            threading.Thread(target=run_batch, args=(items, mode, strength, rate,
                             load_settings(), requested), daemon=True).start()
            self._send(200, {"ok": True})
        elif u.path == "/api/watch":
            data = self._read_json()
            if "enabled" in data:
                WATCH["enabled"] = bool(data["enabled"])
            if "folder" in data:
                WATCH["folder"] = data["folder"] or ""
                _watch_sizes.clear()
            self._send(200, dict(WATCH))
        elif u.path == "/api/watch/pick-folder":
            folder = pick_folder_path()
            self._send(200, {"folder": folder})
        elif u.path == "/api/cancel":
            JOB.cancel.set()
            self._send(200, {"ok": True})
        elif u.path == "/api/stop-after-current":
            JOB.stop_after.set()
            self._send(200, {"ok": True})
        elif u.path == "/api/reveal":
            path = self._read_json().get("path")
            if path and is_authorized_path(path) and os.path.exists(path):
                self._send(200, {"ok": reveal_files([path])})
            else:
                self._send(404, {"error": "not found"})
        elif u.path == "/api/reveal-log":
            path = self._read_json().get("path")
            if path and is_authorized_path(path) and os.path.isfile(path):
                self._send(200, {"ok": reveal_files([path])})
            else:
                self._send(404, {"error": "failure log not found"})
        elif u.path == "/api/reveal-all":
            with JOB.lock:
                paths = [it.get("out") for it in JOB.items if it.get("out") and os.path.exists(it.get("out"))]
            if paths:
                reveal_files(paths)
            self._send(200, {"ok": bool(paths), "count": len(paths)})
        elif u.path == "/api/open-external":
            # External links are intentionally allow-listed, not user-supplied.
            target = self._read_json().get("target")
            url = {"feedback": ISSUES_URL, "releases": RELEASES_URL}.get(target)
            if not url:
                self._send(400, {"error": "unknown external destination"})
                return
            self._send(200, {"ok": open_url(url)})
        elif u.path == "/api/processed-sample":
            data = self._read_json()
            path = data.get("path")
            with JOB.lock:
                encoding = JOB.running
            if encoding:
                self._send(409, {"error": "Processed samples pause while an export is running."})
                return
            if not path or not is_authorized_path(path) or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            try:
                sample, t, duration = PREVIEWS.render_processed_sample(
                    path, data.get("strength", "Medium"), load_settings(), data.get("t"))
                self._send(200, {"path": sample, "t": t, "duration": duration})
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                self._send(500, {"error": str(exc) or "sample render timed out"})
        elif u.path == "/api/scopes":
            data = self._read_json()
            path = data.get("path")
            if not path or not is_authorized_path(path) or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            token, errors, t, dur = PREVIEWS.render_scopes(path, data.get("strength", "Medium"),
                                                   load_settings(), data.get("t"))
            self._send(200, {"token": token, "name": os.path.basename(path),
                             "errors": errors, "t": t, "duration": dur})
        elif u.path == "/api/filmstrip":
            data = self._read_json()
            path = data.get("path")
            if not path or not is_authorized_path(path) or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            token, times = PREVIEWS.render_filmstrip(path, int(data.get("count", 8)))
            self._send(200, {"token": token, "times": times})
        elif u.path == "/api/banding-meter":
            data = self._read_json()
            path = data.get("path")
            if not path or not is_authorized_path(path) or not os.path.isfile(path):
                self._send(400, {"error": "file not found"})
                return
            self._send(200, analyze_banding(path))
        elif u.path == "/api/compare":
            data = self._read_json()
            src, out = data.get("src"), data.get("out")
            if not (src and out and is_authorized_path(src) and is_authorized_path(out)
                    and os.path.isfile(src) and os.path.isfile(out)):
                self._send(400, {"error": "files not found"})
                return
            dur = probe_duration(src)
            t = data.get("t")
            if t is None:
                t = round(dur * 0.4, 2) if dur else 1.0
            t = float(t)
            if dur:
                t = max(0.0, min(t, max(0.0, dur - 0.05)))
            token = PREVIEWS.render_compare(src, out, t, data.get("zoom"))
            self._send(200, {"token": token, "duration": dur, "t": t})
        else:
            self._send(404, {"error": "unknown"})


def cleanup_temp():
    """Remove intake uploads + scope/compare/filmstrip frame dirs on exit."""
    PREVIEWS.cleanup()
    for d in [INTAKE_DIR, *SCOPES.values(), *COMPARE.values(), *FILMSTRIPS.values()]:
        shutil.rmtree(d, ignore_errors=True)


SERVER = None
SERVER_THREAD = None
SHUTDOWN_LOCK = threading.Lock()


def start_server():
    """Bind the local server before the native GUI claims the main thread."""
    global SERVER, SERVER_THREAD
    last_error = None
    for p in range(PORT, PORT + 20):
        try:
            SERVER = ThreadingHTTPServer((HOST, p), Handler)
            SERVER.daemon_threads = True
            break
        except OSError as e:
            last_error = e
            continue
    if SERVER is None:
        detail = f" ({last_error})" if last_error else ""
        raise RuntimeError(f"Could not bind a local port in {PORT}–{PORT + 19}{detail}")
    SERVER_THREAD = threading.Thread(target=SERVER.serve_forever, name="10bit-http", daemon=True)
    SERVER_THREAD.start()
    host, port = SERVER.server_address[:2]
    return f"http://{host}:{port}/"


def shutdown():
    """Idempotent shutdown used by browser Ctrl-C and native window close."""
    global SERVER
    with SHUTDOWN_LOCK:
        if SHUTDOWN_EVENT.is_set():
            return
        SHUTDOWN_EVENT.set()
        WATCH["enabled"] = False
        JOB.cancel.set()
        with JOB.lock:
            proc = JOB.proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if SERVER:
            try:
                SERVER.shutdown()
                SERVER.server_close()
            except OSError:
                pass
            SERVER = None
        cleanup_temp()
        if INSTANCE_GUARD:
            INSTANCE_GUARD.release()


def launch_native(url):
    """Return True only when a native PyWebView session actually ran."""
    global NATIVE_WINDOW, NATIVE_WEBVIEW
    def load_state():
        initial = load_window_state()
        with WINDOW_STATE_LOCK:
            WINDOW_STATE.update(initial)
        return initial

    def set_handles(window, webview):
        global NATIVE_WINDOW, NATIVE_WEBVIEW
        NATIVE_WINDOW, NATIVE_WEBVIEW = window, webview

    return run_native_window(url, load_state, remember_window_size,
                             remember_window_position, save_window_state,
                             shutdown, set_handles,
                             lambda: JOB.running, JOB.cancel.set)


def main():
    global INSTANCE_GUARD
    bundled_ready = ensure_bundled_ffmpeg()
    if not bundled_ready and getattr(sys, "frozen", False):
        show_missing_dependency_error()
        return
    ensure_app_support_dir()
    INSTANCE_GUARD = InstanceGuard(os.path.join(APP_SUPPORT_DIR, "app.lock"))
    if not INSTANCE_GUARD.acquire():
        activate_existing(APP_BUNDLE_ID)
        return
    atexit.register(INSTANCE_GUARD.release)
    atexit.register(cleanup_temp)
    load_update_state()
    # This is the only background network task: one public GitHub Release
    # metadata request every seven days, never while an export is running.
    threading.Thread(target=check_weekly_update_if_due, daemon=True).start()
    if not bundled_ready:
        # Developer/browser mode can still use a system FFmpeg. The packaged
        # app above is deliberately stricter and never relies on it.
        if not shutil.which("ffmpeg"):
            print("ERROR: bundled FFmpeg is missing and no system FFmpeg was found.")
            return
        print("WARNING: bundled FFmpeg is missing; using the developer machine's FFmpeg.")
    threading.Thread(target=watch_loop, daemon=True).start()
    try:
        url = start_server()
    except RuntimeError as e:
        print(e)
        return
    print(f"8-bit → 10-bit converter running at {url}")
    if "--browser" not in sys.argv and launch_native(url):
        return
    open_url(url)
    try:
        while not SHUTDOWN_EVENT.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        shutdown()


if __name__ == "__main__":
    main()
