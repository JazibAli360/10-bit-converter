# Optional libplacebo FFmpeg build spike

This directory is deliberately a build specification, not a replacement for
the currently shipped FFmpeg. The release bundle remains the proven CPU
`ffmpeg-deband-v1` pipeline until the spike passes on the target M3 hardware.

## Reproducible candidate build

1. Pin an FFmpeg release and a matching libplacebo/MoltenVK source revision;
   record the source URLs, commit IDs, SHA-256 checksums, and licence notices
   in `build-info.txt` before compiling. Do not use a floating `main` branch.
2. Build arm64 in a clean macOS environment with a private prefix and no
   Homebrew runtime dependency. The configure proof must include:

   ```text
   --enable-vulkan --enable-libplacebo
   ```

3. Capture `ffmpeg -hide_banner -buildconf` and
   `ffmpeg -hide_banner -filters` in the candidate output. The filter list must
   contain `libplacebo`, and `otool -L` must identify every Vulkan/MoltenVK
   dependency that will be bundled and signed.
4. Place the candidate pair only under `ffmpeg-build/out/arm64/` and run a
   ten-second 1080p fixture through the conservative filter graph described in
   `IMPLEMENTATION_ROADMAP.md`. Confirm `yuv420p10le`, unchanged dimensions and
   duration, retained audio/metadata, and a usable Vulkan device on an M3.
5. Record median and 1%-low FPS, memory pressure, and thermal behavior against
   the current CPU engine. Stop and roll back if GPU initialization is
   unreliable, output shimmers/changes colour tags, or throughput regresses
   without a visible quality benefit.

The app currently probes the installed/bundled FFmpeg at runtime and reports
the experimental backend as unavailable when these checks are not true. It
never downloads a model, silently falls back, or changes the Faithful 10-bit
default.

## Release gate

The normal release command validates the current bundled FFmpeg and packages
it unchanged. A future candidate release may opt into the stronger gate with
`REQUIRE_LIBPLACEBO=1`; that mode must verify the filter, Vulkan build flags,
dynamic dependencies, code signing, and the corpus QA manifest before shipping.
