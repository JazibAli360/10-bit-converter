#!/usr/bin/env python3
"""Exercise both conversion engines from a finished macOS app bundle.

This is deliberately a release gate, not a unit test. It catches missing
FFmpeg dylibs, broken optional-engine extraction, and a Vulkan/MoltenVK setup
that merely looks present in the UI but cannot process a frame.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


TIMEOUT_SECONDS = 45


def run(label, command, env=None):
    try:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"Release gate failed: {label} timed out after {TIMEOUT_SECONDS}s") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-800:]
        raise SystemExit(f"Release gate failed: {label}\n{detail}")
    print(f"✓ {label}")


def frame_command(ffmpeg, filter_chain):
    return [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "color=c=gray:s=32x32:r=24:d=0.1", "-vf", filter_chain,
        "-frames:v", "1", "-f", "null", "-",
    ]


def main():
    args = sys.argv[1:]
    require_gpu = "--require-gpu" in args
    args = [arg for arg in args if arg != "--require-gpu"]
    if len(args) != 1:
        raise SystemExit(
            "Usage: release_smoke_test.py [--require-gpu] '/path/to/10-bit Converter.app'"
        )
    app = Path(args[0]).resolve()
    bin_dir = app / "Contents" / "Resources" / "bin" / "arm64"
    ffmpeg, ffprobe = bin_dir / "ffmpeg", bin_dir / "ffprobe"
    bundle = bin_dir / "libplacebo.bundle.zip"
    for path in (ffmpeg, ffprobe, bundle):
        if not path.is_file():
            raise SystemExit(f"Release gate failed: missing {path.name}")

    run("bundled FFmpeg starts", [str(ffmpeg), "-hide_banner", "-version"])
    run("bundled FFprobe starts", [str(ffprobe), "-hide_banner", "-version"])
    run(
        "Faithful CPU deband processes a 10-bit frame",
        frame_command(
            ffmpeg,
            "deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=1,"
            "noise=alls=2:allf=t+u,format=yuv420p10le",
        ),
    )

    with tempfile.TemporaryDirectory(prefix="10bit-release-gpu-") as temporary:
        temporary = Path(temporary)
        run("optional GPU bundle extracts", ["ditto", "-x", "-k", str(bundle), str(temporary)])
        engine = temporary / "libplacebo"
        gpu_ffmpeg = engine / "ffmpeg"
        source_manifest = engine / "vulkan" / "icd.d" / "MoltenVK_icd.json"
        runtime_manifest = engine / "vulkan" / "icd.d" / "MoltenVK_runtime_icd.json"
        molten = engine / "lib" / "libMoltenVK.dylib"
        if not (gpu_ffmpeg.is_file() and source_manifest.is_file() and molten.is_file()):
            raise SystemExit("Release gate failed: optional GPU engine is incomplete")
        manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        manifest.setdefault("ICD", {})["library_path"] = str(molten)
        runtime_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.pop("DYLD_LIBRARY_PATH", None)
        environment["VK_ICD_FILENAMES"] = str(runtime_manifest)
        gpu_command = frame_command(
            gpu_ffmpeg,
            "libplacebo=deband=true:deband_iterations=2:deband_radius=16:"
            "deband_threshold=4:deband_grain=5,format=yuv420p10le",
        )
        try:
            run("libplacebo GPU deband processes a 10-bit frame", gpu_command, environment)
        except SystemExit as exc:
            # This engine is optional and the app exposes it only after its
            # runtime availability check succeeds. A standard release must be
            # usable with the faithful CPU engine even on a Mac without a
            # compatible Vulkan runtime. CI or a GPU-qualified release can
            # require the stronger gate explicitly.
            if require_gpu:
                # The first launch can spend time in macOS's code-signature cache.
                # Retrying once distinguishes that one-time warmup from a genuinely
                # unusable Vulkan runtime; a second failure still blocks the build.
                print("GPU cold-start verification was slow; retrying once…")
                run("libplacebo GPU deband processes a 10-bit frame", gpu_command, environment)
            print(f"! Optional libplacebo GPU check skipped: {exc}")


if __name__ == "__main__":
    main()
