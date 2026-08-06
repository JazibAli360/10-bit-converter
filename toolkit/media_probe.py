"""Media inspection helpers used by the converter and preflight.

This module intentionally has no HTTP or UI knowledge. It shells out only to
ffprobe found on PATH; the application controller selects the bundled tools
before importing/using these helpers.
"""

import json
import os
import subprocess


def pixfmt_bits(pix_fmt):
    for bits in ("16", "12", "10"):
        if bits in (pix_fmt or ""):
            return int(bits)
    return 8


def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def probe_bitrate_kbps(path):
    """Return the video bitrate, falling back to container/file size."""
    for args in (
        ["-select_streams", "v:0", "-show_entries", "stream=bit_rate"],
        ["-show_entries", "format=bit_rate"],
    ):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", *args, "-of", "csv=p=0", path],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if out.isdigit() and int(out) > 0:
                return int(out) // 1000
        except Exception:
            pass
    try:
        duration = probe_duration(path)
        if duration > 0:
            return int(os.path.getsize(path) * 8 / duration / 1000)
    except Exception:
        pass
    return 0


def probe_pix_fmt(path):
    try:
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return ""


def probe_audio_codec(path):
    try:
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return ""


def probe_subtitle_codecs(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        ).stdout
        return [line.strip().lower() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def probe_info(path):
    """One-shot probe: duration, bitrate, resolution, fps, and pixel format."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,pix_fmt,avg_frame_rate,codec_name:format=duration,bit_rate",
             "-of", "json", path], capture_output=True, text=True, check=True,
        ).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format", {})
        duration = float(fmt.get("duration") or 0)
        bitrate = fmt.get("bit_rate")
        if bitrate and bitrate.isdigit() and int(bitrate) > 0:
            kbps = int(bitrate) // 1000
        elif duration > 0:
            kbps = int(os.path.getsize(path) * 8 / duration / 1000)
        else:
            kbps = 0
        fps = 0.0
        frame_rate = stream.get("avg_frame_rate", "0/0")
        if "/" in frame_rate:
            numerator, denominator = frame_rate.split("/")
            fps = float(numerator) / float(denominator or 0) if float(denominator or 0) else 0.0
        pix_fmt = stream.get("pix_fmt", "")
        return {
            "dur": round(duration, 2), "kbps": kbps, "size": os.path.getsize(path),
            "width": stream.get("width", 0), "height": stream.get("height", 0),
            "fps": round(fps, 2), "pix_fmt": pix_fmt,
            "codec": stream.get("codec_name", ""), "bits": pixfmt_bits(pix_fmt),
        }
    except Exception:
        return {"dur": 0, "kbps": 0, "size": 0, "width": 0, "height": 0,
                "fps": 0, "pix_fmt": "", "codec": "", "bits": 0}
