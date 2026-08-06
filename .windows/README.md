# Windows x64 release

## Project status — contributions welcome

The Windows build path is scaffolded, but incomplete: it has not been built,
tested, or released from a Windows machine yet. The maintainer does not
currently have access to Windows hardware. If you do, you are welcome to make
your own build, validate it, improve it, and share the work back through a
pull request or issue.

Everything needed to attempt a build, test, and package the Windows version
lives here. The app remains local-only: source videos are processed on the
creator's machine and never uploaded by the desktop app.

## Two release modes

| Build | Engine | Use it for |
|---|---|---|
| Default | Faithful CPU 10-bit (`ffmpeg-deband-v1`) | Every compatible Windows machine; stable delivery release. |
| `-IncludeGpu` | libplacebo + Vulkan (`libplacebo-deband-v1`) | Creators with tested Vulkan-capable GPUs; UI shows it only after a real GPU frame succeeds. |

GPU mode is optional and capability-gated. If the computer has no compatible
Vulkan driver, the app keeps the standard CPU engine available rather than
silently producing a different result or failing the queue.

## Release inputs

Put the standard FFmpeg pair in `.windows/bin/win-x64/`. For GPU packaging,
also add the fully self-contained libplacebo/Vulkan FFmpeg bundle described in
[`bin/win-x64/README.md`](bin/win-x64/README.md). The app copies the optional
engine from the installed folder into LocalAppData at first use, which allows
a correct per-user Vulkan runtime configuration without any user-facing zip.

## Build on Windows 10/11 x64

Install 64-bit Python 3.11, Microsoft Edge WebView2 Runtime, and Inno Setup 6.
Then run PowerShell at the project root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.windows\build-release.ps1 -Version 0.1.0
```

For a GPU-qualified release:

```powershell
.\.windows\build-release.ps1 -Version 0.1.0 -IncludeGpu
```

The GPU command stops if that Windows machine cannot create a Vulkan device
and run a 10-bit libplacebo frame. This is intentional: it prevents packaging
an option that merely appears in the UI but fails for creators.

Both commands produce a shareable installer in `dist/`; neither creates a zip.
Use `-SkipInstaller` only when you need the portable app directory for QA.

## Release checklist

1. Test a clean Windows account with no Python or FFmpeg on `PATH`.
2. Test Add Files, Add Folder, native drag/drop, custom output, cancellation,
   output reveal, long paths, external drives, and collision handling.
3. For GPU releases, test at least one NVIDIA, AMD, and Intel Vulkan-capable
   system, plus a no-compatible-GPU system to verify the CPU fallback.
4. Sign both the installer and `10-bit Converter.exe` with Authenticode before
   public distribution. Signing improves SmartScreen treatment, though
   reputation builds over time.
5. Record the FFmpeg/libplacebo/Vulkan build sources and license notices with
   the release; confirm whether the exact FFmpeg build is LGPL or GPL.
