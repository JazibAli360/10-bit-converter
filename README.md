# 10-bit Converter

A local macOS desktop app for making 8-bit AI video easier to grade.

AI-generated clips can look great until a smooth sky, shadow, fog layer, or
skin-tone gradient starts showing obvious colour steps. 10-bit Converter uses
a deband + dither pass and exports a 10-bit intermediate so those gradients
look cleaner and are less likely to break further during grading.

It does **not** recover colour information that was never present in an
8-bit source. It is a practical banding-reduction and 10-bit delivery tool,
not a detail-restoration model.

## Product film

**[▶ Play the 10-bit Converter product film](https://jazibali360.github.io/10-bit-converter/)**

GitHub README files do not support inline MP4 playback, so the film has its own
playable product page. You can also download the MP4 from the
[current GitHub Release](https://github.com/JazibAli360/10-bit-converter/releases/latest).

## What it does

- Processes videos locally — no uploads, accounts, or cloud rendering.
- Queues individual videos or folders and keeps each output beside its source
  (or in a chosen output directory).
- Produces HEVC Main10 for smaller delivery files or ProRes 4444 for grading.
- Applies configurable FFmpeg deband, dither, denoise, deflicker, and
  colour-management settings.
- Provides preflight checks, cancellation, per-file progress, output safety,
  conversion history, scopes, filmstrips, and before/after comparison tools.
- Includes an optional experimental libplacebo/Vulkan GPU engine, which is
  capability-checked before the UI offers it.

## Tech

- Python local controller and HTTP server
- PyWebView native desktop shell
- HTML/CSS/JavaScript interface
- FFmpeg / FFprobe conversion and media analysis
- `libx265` HEVC Main10 and ProRes 4444 exports
- Optional libplacebo + Vulkan GPU deband path

## Status

The current packaged release target is Apple Silicon macOS. Windows build and
GPU packaging tooling lives in [`.windows/`](.windows/), but a final Windows
installer needs to be built and validated on Windows hardware.

## Downloads and product film

Download the current macOS build and watch the product film from the
[GitHub Releases page](https://github.com/JazibAli360/10-bit-converter/releases).
The release asset is an Apple Silicon (`arm64`) macOS app archive.

## Run or build

The full macOS source/build notes are in [toolkit/README.md](toolkit/README.md).

Want a Windows build? Start with [`.windows/README.md`](.windows/README.md),
or hand the copy-ready brief in [`.windows/AGENT_BUILD.md`](.windows/AGENT_BUILD.md)
to an AI coding agent on a Windows x64 machine.

## Forking, agents, and brand context

- Start with [AGENTS.md](AGENTS.md) for architecture, safety constraints, and
  verification requirements.
- Use [docs/BRAND.md](docs/BRAND.md) for honest product language, visual
  direction, attribution, and release copy.
- Use [docs/PRODUCT_STORY.md](docs/PRODUCT_STORY.md) and
  [docs/SOURCES.md](docs/SOURCES.md) for the credited case-study narrative,
  research ledger, and product-film guidance.

The repository intentionally excludes packaged `.app` bundles, FFmpeg binaries,
test videos, and other large local artifacts. Add your own redistributable
FFmpeg/FFprobe binaries when building a standalone app, and follow their
licensing requirements.

## License

This project is released under the [MIT License](LICENSE). Use it, change it,
learn from it, ship it inside another project, or make something much better
with it. Keep the license notice with substantial copies of the code.

## A quick honest note

This was vibe-coded into a genuinely useful tool. The core conversion flow has
regression tests, but please review it for your own production use, open an
issue if something is off, and send improvements back if you feel like it.
