# Give this to a Windows build agent

Copy the prompt below to an AI coding agent that has access to a **Windows 10
or 11 x64 machine**. The agent must use that machine for the actual build and
GPU validation; macOS cannot produce a truthful Windows desktop release.

```text
You are packaging a public Windows x64 release of 10-bit Converter.

Repository: https://github.com/JazibAli360/10-bit-converter
Read .windows/README.md and follow it exactly.

Goal:
1. Build a signed-ready Windows x64 installer, not a zip.
2. Bundle the normal CPU FFmpeg engine with libx265, deband, and 10-bit pixel
   format support.
3. If a self-contained libplacebo/Vulkan FFmpeg bundle is supplied, also build
   the capability-gated GPU release with -IncludeGpu.
4. Run .windows/windows_release_smoke_test.py against the finished app. A GPU
   build must successfully render a 10-bit libplacebo frame on the real target
   GPU; do not ship the GPU option if that test fails.
5. Use Inno Setup 6 to produce a shareable installer in dist/.
6. Do not add FFmpeg binaries, generated build folders, test media, credentials,
   or temporary artifacts to Git. Record the exact FFmpeg/libplacebo source,
   version, and licence notices for the release.

Expected command:
  .\.windows\build-release.ps1 -Version 0.1.0 -IncludeGpu

If GPU inputs or a compatible Vulkan device are unavailable, make the normal
CPU installer with:
  .\.windows\build-release.ps1 -Version 0.1.0

Report: the installer path, FFmpeg version/build flags, GPU test result,
hardware/driver used, SHA-256 checksum, and any missing prerequisites.
```

## Required Windows inputs

The Windows build machine needs:

- 64-bit Python 3.11
- Microsoft Edge WebView2 Runtime
- Inno Setup 6
- Standard redistributable `ffmpeg.exe` and `ffprobe.exe` in
  `.windows/bin/win-x64/`
- For GPU: the self-contained `libplacebo/` bundle described in
  [README.md](README.md), plus a compatible Vulkan driver

The build process never uploads source video. It creates a local installer in
`dist/`, ready for the owner to sign with Authenticode before public release.
