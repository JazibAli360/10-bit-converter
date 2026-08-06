# Windows x64 FFmpeg release input

Place the verified, redistributable Windows release inputs here:

```text
ffmpeg.exe
ffprobe.exe
libplacebo/                 # optional, for a GPU release
  ffmpeg.exe
  ffprobe.exe
  *.dll or lib/*.dll
  vulkan/icd.d/*.json        # only when the bundle supplies an ICD
```

The standard pair must include `libx265`, `deband`, and 10-bit pixel formats.
The optional `libplacebo` pair must include the `libplacebo` FFmpeg filter and
every DLL it needs. It is packaged as a writable runtime bundle, then the app
checks Vulkan device creation before showing GPU Deband as selectable.

Record the upstream build and license notices in the release notes. FFmpeg
builds are commonly LGPL, but enabled components can make a build GPL; verify
the exact distribution before publishing.
