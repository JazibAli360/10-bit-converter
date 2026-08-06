# Implementation roadmap

This roadmap describes the current app, not an AI/model experiment. Faithful
FFmpeg remains the default engine. The optional libplacebo bundle is
deterministic GPU video processing, not an ML model, and remains capability-
gated. BasicVSR or another restoration engine stays out of scope.

## Product contract

- The default path is three decisions: add clips, choose **Faithful 10-bit**
  or **Editing master**, then Convert.
- **Advanced** is the only place that exposes codec, CRF, encoder preset,
  dither, range, and custom bitrate controls.
- ProRes never shows bitrate controls.
- Gradient analysis is experimental heuristic guidance. It must never be
  described as AI, automatic truth, or “Smart Auto.”
- Every export is preflighted, collision-safe, written through a temporary
  file, and recorded with exact source and output paths.

## Phase 1 — Split the existing architecture

Current boundary work:

- `export_safety.py`: collision naming, staging-path, and writable destination
  rules. Keep it free of UI and subprocess code.
- `engines/base.py`: the engine protocol and capability contract.
- `engines/ffmpeg_deband.py`: built-in engine identity and strength mapping.
- `server.py`: currently owns HTTP, probing, conversion orchestration, reports,
  previews, scopes, and the native shell. This remains the largest risk.

Next extraction order:

1. Move probing, stream inspection, and duration/size logic to `media_probe.py`.
2. ✓ FFmpeg process supervision lives in `conversion_runner.py`; per-item
   profile resolution, stream preservation, colour tags, output paths, rate
   arguments, and filter selection now live in `conversion_service.py`.
   Neither module knows about HTTP.
3. Move preflight and output prediction to `preflight.py`. The browser and
   conversion runner must consume the same resolved output plan.
4. Move reports/history to `history_store.py`, using atomic writes and exact
   input/output/profile/error fields.
5. ✓ Sample, compare, filmstrip, and scopes rendering now live in
   `preview_service.py`. The server retains only authorization, route handling,
   and local artifact serving. All work remains user-triggered and processed
   samples remain blocked while an export is running.
6. Leave `server.py` as route validation and application composition only.

Gate: unit tests can exercise every extracted module without starting a
window, HTTP server, or real long-running FFmpeg encode.

## Phase 2 — Split the UI

`index.html` is still mostly a monolith. The authenticated request boundary is
now isolated in `ui/api.js`; split the remaining UI only after the current redesign is
stable so the refactor does not hide interaction regressions.

Suggested files and ownership:

- `ui/index.html`: semantic application shell and modal containers.
- `ui/styles/tokens.css`: colour, spacing, typography, focus, light/dark tokens.
- `ui/styles/app.css`: queue, inspector, completion, responsive layout.
- `ui/api.js`: authenticated requests and typed error normalization.
- `ui/state.js`: queue/global/per-video state and persistence schema.
- `ui/queue.js`: incremental row creation/patch/removal and selection.
- `ui/profiles.js`: Faithful, Editing master, and Advanced state transitions.
- `ui/inspector.js`: selected-video details and processed-sample actions.
- `ui/conversion.js`: preflight, sticky bar, polling, and completion screen.
- `ui/modals.js`: focus trapping, Escape, menus, and settings visibility.
- `ui/previews.js`: native player, compare, scopes, and experimental analysis.

Rules during the split:

- Do not rebuild the entire queue for progress-only updates.
- Keep one source of truth for global and per-video settings.
- Preserve native file-drop handling and path authorization.
- Add explicit empty, loading, ready, running, failed, cancelled, and completed
  UI states; never infer them from button text.
- Every menu must remain within the viewport and close on Escape/outside click.

Gate: the browser interaction suite passes at 680 px and desktop widths with no
horizontal overflow, duplicate imports, stale queue count, or hidden controls
becoming keyboard-focusable.

## Phase 3 — Engine abstraction

The first boundary now exists under `engines/`. Continue by making the runner
accept a `ConversionEngine` instance rather than branching on engine names.
Each engine must declare:

- stable ID and user-facing name;
- supported input/output formats;
- whether processed samples and gradient analysis are available;
- command construction and output validation;
- estimated compute, memory, and disk requirements;
- cancellation and progress behavior.

There is no model download UI, model selector, or BasicVSR code in this phase.
Faithful 10-bit remains the built-in FFmpeg `deband` + dither + 10-bit path.

## Phase 3.5 — Optional libplacebo backend

### Decision and guardrails

`libplacebo` is a deterministic GPU renderer with debanding, dithering,
high-quality scaling, and colour-management tools. It is not an AI model and
must never be described as one. It is a candidate for an optional advanced
backend only; it must not replace **Faithful 10-bit**, alter its defaults, or
silently fall back to another processing method.

Initial product name: **High-quality GPU deband (experimental)**.

- Show it only in Advanced, never as one of the two default profile choices.
- Default to colour-preserving operation: no brightness, contrast, saturation,
  gamma, temperature, HDR tone mapping, gamut conversion, scaling, or custom
  shader controls.
- Expose only deband iterations, radius, threshold, and grain after an
  explicit Experimental disclosure.
- Do not claim it restores colour depth. It only smooths quantisation steps and
  uses dither/grain to make later 10-bit quantisation more robust.
- If the backend cannot initialize, block that export with a clear error and a
  one-click switch back to Faithful 10-bit. Never silently change the engine.

### Build spike — required before product code

1. Create `toolkit/ffmpeg-build/README.md` with exact source revisions,
   configure flags, SHA-256 checksums, licences/notices, and a reproducible
   arm64 build command. Do not overwrite the current bundled FFmpeg.
2. Produce a separate arm64 candidate binary pair under
   `toolkit/ffmpeg-build/out/arm64/`. The FFmpeg configure step must prove
   `--enable-vulkan` and `--enable-libplacebo`; capture `ffmpeg -buildconf` in
   `build-info.txt`.
3. On an M3 machine, verify the candidate has both `libplacebo` in
   `ffmpeg -filters` and a usable Vulkan device. The macOS Vulkan path must be
   explicitly documented, including any MoltenVK loader/framework and shader
   dependencies that need bundling and signing.
4. Run one 1080p ten-second source through a minimal libplacebo filter graph.
   Confirm the command runs on the intended GPU path, produces a 10-bit file,
   and does not depend on Homebrew, a system Vulkan install, or network access.
5. Stop here if the candidate needs a CPU fallback, has unstable output, or is
   materially slower than the current CPU backend on the target M3 hardware.

The spike is a hard gate. No UI, engine selection, or release bundle work
begins until all five checks pass.

The current implementation packages the candidate as an isolated
`libplacebo.bundle.zip` and performs the runtime Vulkan probe before enabling
the selector. On systems where the ICD cannot initialize, the bundle remains
present for diagnostics but the selector stays disabled and conversions remain
on Faithful 10-bit.

### File-by-file implementation after the spike

| File | Change |
| --- | --- |
| `toolkit/engines/base.py` | Extend `EngineCapabilities` with `requires_gpu_backend`, `supports_colour_management`, and a structured availability check/result. |
| `toolkit/engines/libplacebo_deband.py` | Add `LibplaceboDebandEngine`: stable ID, filter-graph builder, default-safe parameters, validation, and an explicit unavailable reason. Keep it separate from `FFmpegDebandEngine`. |
| `toolkit/engines/__init__.py` | Register libplacebo only when the availability check succeeds; Faithful remains the default engine unconditionally. |
| `toolkit/conversion_runner.py` (extract from `server.py` first) | Accept an engine instance and let it construct the video-filter/device arguments. Keep stream mapping, metadata, staging files, collision policy, progress parsing, cancellation, and output verification engine-neutral. |
| `toolkit/server.py` | Add a read-only `/api/engines` capability response and validate a requested engine ID. Reject unavailable IDs before preflight. Never put Vulkan/libplacebo command fragments directly in route handlers. |
| `toolkit/index.html` / later `ui/profiles.js` | Add the experimental backend only inside Advanced. Display exact availability, expected performance, and “preserves colour settings; does not create colour information.” Hide it for per-video settings until global-engine behavior is proven. |
| `toolkit/setup_native.py` | Bundle the proven arm64 FFmpeg/FFprobe plus every required Vulkan/libplacebo/MoltenVK dynamic dependency. Do not copy arbitrary system frameworks. |
| `build_release.sh` | Fail the release if the bundled binary lacks `libplacebo`, Vulkan validation, or required dynamic libraries. Sign and verify each nested executable/framework before signing the app. |
| `toolkit/README.md` | Explain the experimental status, exact hardware requirement, colour-preserving defaults, failure behavior, and how to return to Faithful 10-bit. |

### Safe first filter profile

The first profile must use only libplacebo debanding and dithering. It must
preserve source dimensions and tagged colourimetry, output `yuv420p10le`, and
retain the existing audio/metadata/chapter/subtitle policy. It must not enable
scaling, tone mapping, colour adjustment, HDR peak detection, temporal
dithering, or external shaders.

Start with conservative values and benchmark them, rather than exposing a
large set of expert knobs:

- deband iterations: 2;
- deband radius: 16;
- deband threshold: 4;
- deband grain: 4–6;
- blue-noise dithering, non-temporal.

These are a benchmark starting point, not a promised default. The exact values
ship only if the quality gate below passes.

### Quality, performance, and failure gates

Build a versioned `toolkit/qa/libplacebo-corpus/manifest.json` containing at
least 24 short clips: skies, smoke, skin, dark gradients, animation, text,
fast motion, heavy compression, SDR, HDR-tagged footage, and 8-/10-bit inputs.
Do not store copyrighted customer clips in the repository; store local paths,
source rights, and reproducible synthetic fixtures instead.

For each clip compare current Faithful output against libplacebo output at the
same codec/rate settings:

1. Inspect a frame wipe and 200–400% crop for residual steps, edge softening,
   chroma bleed, grain, halos, and flicker.
2. Verify source dimensions, 10-bit pixel format, duration, frame count, audio
   stream count, metadata, chapters, and supported subtitles are retained.
3. Measure median and 1%-low encode FPS on M3 8GB, 16GB, and 24GB/Pro-class
   hardware where available. Record memory pressure and thermal throttling.
4. Reject the backend if any clip has repeatable temporal shimmer, colour-tag
   changes, unexpected scaling/tone mapping, GPU initialization failures, or a
   material throughput regression without a clearly visible quality benefit.
5. Test cancellation, Stop after current, full disk, collision policies,
   background/foreground app state, app relaunch, and no-network clean-account
   launch with the experimental engine selected.

Release only after the corpus and packaged-app gates pass. Otherwise retain the
candidate in a separate build branch and ship only Faithful 10-bit.

### Rollback and support policy

- The existing `ffmpeg-deband-v1` engine remains bundled and selected by
  default forever unless a later explicit product decision changes it.
- Persist an engine ID per queue item; on relaunch, an unavailable experimental
  engine is shown as unavailable and never auto-substituted.
- Include the engine ID, libplacebo/FFmpeg build information, effective filter
  settings, GPU availability result, and fallback reason in reports/history.
- A support issue must be reproducible by selecting Faithful 10-bit first; the
  experimental backend never becomes the sole path to a conversion.

## Testing requirements

Automated tests must cover:

- overwrite, skip, and rename collision policies;
- staging-file cleanup on success, failure, timeout, and cancellation;
- FFmpeg timeout plus continuous stderr draining;
- preservation of primary video, all audio, metadata, chapters, and supported
  subtitle/data streams;
- ProRes never receiving bitrate/CRF/two-pass arguments;
- accurate output estimates that refresh when profile or bitrate changes;
- global versus per-video settings, reset, duplicate export, and persistence;
- duplicate drag/drop suppression and queue count after every removal path;
- preflight blocking for missing files, unwritable folders, collisions, and
  insufficient disk space;
- report/history exact input and output paths, Reveal, Retry, and Copy Path;
- profile visibility: advanced controls absent until Advanced is selected;
- completion summary counts, written size, destination, reveal, and retry;
- responsive layout at minimum native window size and 680 px browser fallback.
- experimental-engine availability, explicit failure when libplacebo/Vulkan is
  missing, no silent fallback, and correct persistence per queue item;
- libplacebo output retains 10-bit format, source colour tags, dimensions,
  duration, frame count, audio, metadata, chapters, and supported subtitles.

Manual packaged-app gates:

1. Launch in a clean macOS account with no Python, Homebrew, FFmpeg, or network.
2. Import by Add, double-click, Finder drop, multi-file drop, and folder.
3. Convert mixed HEVC/ProRes per-video profiles, including collisions.
4. Cancel and force an FFmpeg failure; verify no final file is corrupted.
5. Quit/relaunch; verify queue, overrides, settings, and history persist.
6. Verify keyboard focus, VoiceOver names, light/dark mode, small window, Finder
   Reveal, notifications, sleep prevention, and bundled app icon.
