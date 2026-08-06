#!/usr/bin/env python3
"""Release gate for a finished Windows one-directory app build.

Run this on the intended Windows hardware after PyInstaller and before
signing/distributing the installer. It proves the bundled CPU pipeline works;
``--require-gpu`` additionally proves libplacebo can create a Vulkan device
and process one 10-bit frame on that exact machine.
"""

import os
import subprocess
import sys
from pathlib import Path


def run(label, command, env=None):
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=45)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-800:]
        raise SystemExit(f"Release gate failed: {label}\n{detail}")
    print(f"✓ {label}")


def frame_command(ffmpeg, filter_chain):
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=gray:s=32x32:r=24:d=0.1", "-vf", filter_chain,
            "-frames:v", "1", "-f", "null", "-"]


def main():
    args = sys.argv[1:]
    require_gpu = "--require-gpu" in args
    args = [arg for arg in args if arg != "--require-gpu"]
    if len(args) != 1:
        raise SystemExit("Usage: windows_release_smoke_test.py [--require-gpu] 'C:\\path\\to\\10-bit Converter'")
    app = Path(args[0]).resolve()
    executable = app / "10-bit Converter.exe"
    ffmpeg = app / "bin" / "win-x64" / "ffmpeg.exe"
    ffprobe = app / "bin" / "win-x64" / "ffprobe.exe"
    for expected in (executable, ffmpeg, ffprobe, app / "index.html", app / "ui" / "queue.js"):
        if not expected.is_file():
            raise SystemExit(f"Release gate failed: missing {expected}")
    run("bundled FFmpeg starts", [str(ffmpeg), "-hide_banner", "-version"])
    run("bundled FFprobe starts", [str(ffprobe), "-hide_banner", "-version"])
    run(
        "Faithful CPU deband processes a 10-bit frame",
        frame_command(ffmpeg, "deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv420p10le"),
    )

    gpu_ffmpeg = app / "bin" / "win-x64" / "libplacebo" / "ffmpeg.exe"
    if not gpu_ffmpeg.is_file():
        if require_gpu:
            raise SystemExit("Release gate failed: GPU release requested but libplacebo/ffmpeg.exe is missing")
        print("! Optional GPU check skipped: no libplacebo bundle")
        return
    environment = os.environ.copy()
    gpu_dir = str(gpu_ffmpeg.parent)
    library_dir = gpu_ffmpeg.parent / "lib"
    environment["PATH"] = os.pathsep.join([gpu_dir, str(library_dir), environment.get("PATH", "")])
    run(
        "libplacebo GPU deband processes a 10-bit frame",
        frame_command(gpu_ffmpeg, "libplacebo=deband=true:deband_iterations=2:deband_radius=16:deband_threshold=4:deband_grain=5,format=yuv420p10le"),
        environment,
    )


if __name__ == "__main__":
    main()
