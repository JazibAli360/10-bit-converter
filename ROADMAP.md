# 10-bit Converter — Roadmap

Planning doc for the 8-bit → 10-bit toolkit. Internal (not shipped in the
release zip). Current shipping version: **v12** (browser app: deband + dither +
true 10-bit, batch queue, scopes, A/B compare, verify, bitrate, deflicker,
16-bit max quality, audio copy, colour passthrough, drag-and-drop).

## Guiding principles
- **No AI, no reinterpretation.** Nothing that invents, removes, or reinterprets
  detail/texture. Precision-only processing.
- **Quality over speed.** No hardware/fast encode mode — x265 quality is the point.
- **Local, private, zero-install.** Bundled ffmpeg, **Apple Silicon (arm64) only**
  — no Intel bundle.
- **Self-explanatory UX.** Every control readable at a glance; sensible defaults;
  live feedback; honest about what it does and doesn't do.
- **Credit:** Built by **Jazib Ali 360**.

---

## Phase 1 — Shippable & robust  (do first)

- [ ] **1.1 Resilient server** — if port 8766 is busy (stale/second instance),
  auto-pick the next free port; open the actual URL; clean shutdown on quit/Ctrl-C.
- [ ] **1.2 Surface ffmpeg errors in the UI** — on failure, show the ffmpeg error
  tail in the app (row "why?" / modal) instead of a bare "Failed".
- [ ] **1.3 Temp cleanup** — remove intake (dropped files) + scope/compare temp
  dirs on exit; cap accumulation.
- [ ] **1.4 `.app` launcher with icon** — double-clickable `10-bit Converter.app`
  that starts the server in the background, opens the browser, and quits cleanly
  (no lingering raw Terminal). Custom `.icns`. Self de-quarantine where possible.
  - **Unsigned note:** runs directly when built locally; a *downloaded* copy needs
    one-time right-click → Open. Fully frictionless = Apple Developer account
    ($99/yr) for signing + notarization — **optional future step**.
  - **arm64 only** — confirmed, no Intel bundle.
- [ ] **1.5 Release zip build script** — one command to assemble `toolkit/` + `bin/`
  into a clean, correctly-permissioned, shareable zip with a first-run note.

## Phase 2 — UX completeness & polish

- [ ] **2.1 Overall batch progress** — "file 2/5 · 41% overall" + a batch bar and
  batch ETA (alongside the per-file readout).
- [ ] **2.2 Reveal outputs** — per-row "Reveal in Finder" (currently only the last
  file is revealed); open-output-folder action.
- [ ] **2.3 Preset profiles** — one-click bundles of settings:
  - **Delivery** — HEVC · Match source · Medium deband
  - **Grading** — ProRes 4444 · High deband · 16-bit max quality
  - **Max preservation** — copy audio · 16-bit · high/custom bitrate
- [ ] **2.4 In-app Help / About** — the honest "what this does / does NOT do"
  (kills banding, adds grading headroom, adds dither; does NOT recover detail)
  shown inside the app, not just the README.
- [ ] **2.5 Credits** — "Built by Jazib Ali 360" in the header/About.
- [ ] **2.6 Sliders for all numeric settings** — replace number inputs with sliders
  that show the **live value while dragging** plus a one-line meaning: CRF,
  dither amount, deband range, custom bitrate (Mbps).
- [ ] **2.7 Accurate estimated output size** — per-file and batch total:
  - **Bitrate modes (Match / Custom)** → exact: (video + audio bitrate) × duration.
  - **ProRes 4444** → from the codec data-rate table (res/fps).
  - **Quality (CRF)** → honest range/estimate (content-dependent), not a fake exact.
- [ ] **2.8 Bitrate guidance on the slider** — visual markers for **source bitrate**,
  a **recommended band**, and an **"overkill / diminishing returns"** zone so the
  user can see how much is too much for the given source.
- [ ] **2.9 Self-explanatory everywhere** — clear labels, inline hints/tooltips,
  good defaults; no control that needs the README to understand.

## Phase 3 — Optional quality power-ups

- [ ] **3.1 Denoise toggle** — `hqdn3d` / `nlmeans` for compression / mosquito
  noise; opt-in, flagged "softens slightly."
- [ ] **3.2 Two-pass encode** — *if needed*, for accurate target bitrate in
  Match/Custom modes.
- *(No fast / hardware-encode mode — quality first, by decision.)*

## Phase 4 — Reach (later)

- [ ] **4.1 Windows / Linux support** — web app is ~90% portable; work is per-OS
  file dialogs (osascript → PowerShell / zenity), reveal (`open -R` → `explorer` /
  `xdg-open`), per-OS ffmpeg bundles, and launchers. "Figure out later."

---

## Additional candidates (proposed — to confirm)
- Per-file **source info** (resolution · codec · bit depth · duration · bitrate) in
  the row or on hover, so you see what you're feeding in.
- **Output filename preview** before converting.
- **Persist last-used** Format / Deband / Bitrate selections across launches
  (settings persist today; the top-level picks reset).
- **"Stop after current file"** (graceful finish) vs the existing **"Cancel now."**
- **Confirm before overwrite** when overwrite mode is on.
- **Convert/error log** written to disk for troubleshooting.
- **Version number** shown in the app.
- **Drag-to-reorder** the queue.

## Out of scope (won't do)
- AI debanding / deflicker (reinterprets detail).
- Sharpening, upscaling, trim/edit, broad format conversion.
- Cloud / upload — stays local and private.
- Intel (x86_64) bundle.
