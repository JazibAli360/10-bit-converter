# 10-bit Converter — agent handoff

This repository is a local video-finishing tool, not an AI restoration model.
Before changing a feature, preserve the product contract below.

## Product truth

- The product reduces the *visibility* of existing 8-bit gradient banding,
  adds controlled dither, and creates a true 10-bit output.
- It does **not** recover missing colour values, texture, detail, or “hidden
  10-bit information.” Never claim that it does.
- Processing stays local. Do not add uploads, accounts, cloud rendering,
  telemetry, model downloads, or automatic AI enhancement without an explicit
  product decision.
- **Faithful 10-bit** is the default: FFmpeg deband + dither + 10-bit encode.
- The libplacebo/Vulkan path is optional, experimental, capability-gated, and
  never silently replaces the default engine.

## Architecture

| Area | Responsibility |
| --- | --- |
| `toolkit/server.py` | Local authenticated HTTP API and application composition. |
| `toolkit/conversion_service.py` | Per-item conversion planning and command construction. |
| `toolkit/conversion_runner.py` | FFmpeg process lifecycle, progress, cancellation, and timeouts. |
| `toolkit/export_safety.py` | Collision policy, staging output, and destination safety. |
| `toolkit/media_probe.py` | FFprobe-derived media facts. |
| `toolkit/engines/` | Engine identity, filter chains, and capability checks. |
| `toolkit/preview_service.py` | Scopes, filmstrip, frame compare, and sample artifacts. |
| `toolkit/index.html` + `toolkit/ui/` | Local web UI and interaction modules. |
| `toolkit/native_lifecycle.py` | PyWebView desktop shell. |
| `.windows/` | Windows packaging, GPU release path, and agent build brief. |

## Non-negotiable engineering rules

1. Keep file operations path-authorized and loopback API requests tokenized.
2. Never overwrite a final export directly: use a same-folder staging path,
   then atomically move it into place after output verification.
3. Preserve the primary video, supported audio, metadata, chapters, and safe
   subtitle behaviour unless a user-facing change explicitly says otherwise.
4. Keep cancellation reliable and remove partial output on failure/cancel.
5. Keep browser fallback functional whenever changing native shell behavior.
6. Do not add FFmpeg binaries, `.app` bundles, test videos, generated output,
   credentials, or large media to Git.
7. Do not describe the GPU path as AI. Libplacebo is deterministic GPU video
   processing, not learned restoration.

## Verify changes

```bash
cd toolkit
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

For a macOS release, run the bundled-app smoke test against the final `.app`.
For a Windows build, read `.windows/README.md` and run the required smoke test
on the actual Windows hardware. Do not claim a Windows or GPU release passed
without that machine-level validation.

## Where to put things

- Product and contributor decisions: `README.md`, `AGENTS.md`, `docs/`.
- Native macOS code/build changes: `toolkit/` and `build_release.sh`.
- Windows-only tooling: `.windows/`.
- Product-study site: `case-study-10-bit-converter/`.
- Release media: GitHub Releases, not the Git repository.

Read [docs/BRAND.md](docs/BRAND.md) before changing product copy, visuals, or
release messaging.
