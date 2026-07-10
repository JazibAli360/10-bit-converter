# Redesign & Native-App Plan

Everything left to do to (1) implement the approved UI redesign in the real app
and (2) turn it into a native macOS app. Written to be picked up cold, in a
future session, without needing this conversation's context.

## Where things stand right now
- **Mockup is approved.** Lives at `design/approved_mockup_v1.html` (open it
  directly in a browser — no server needed, it's fully static/self-contained).
  It shows 4 states: empty/first-launch, idle-queued, converting, just-finished.
- **The real app is NOT yet updated.** `toolkit/index.html` (925 lines) and
  `toolkit/server.py` (1203 lines) still have the *old* cluttered toolbar.
  Nothing described in Phase A below has been built into the real app yet —
  only mocked up.
- **The icon is decided.** `Imagine Art - Icon.png` (534×534 PNG) sits at the
  project root. It's already embedded in the mockup as a 64×64 base64 PNG in
  the title bar. Not yet wired into the real app.
- Every feature in `ROADMAP.md` (v1–v29) is shipped and working. This document
  is the *next* arc of work, layered on top of that.

## Guiding principles (unchanged from ROADMAP.md)
- No AI, no reinterpretation of detail/texture. Quality over speed — no
  hardware/fast-encode mode. Local, private, zero-install. Apple Silicon
  (arm64) only. Built by **Jazib Ali 360**.
- Progressive disclosure: show the ~5 things everyone uses; everything else
  lives behind hierarchy (menus, expanders, per-row hover actions).
- Reuse, don't rewrite — the native app (Phase B+) wraps the *existing*
  `server.py` + `index.html`; no second codebase, no framework rewrite.
- **No "no AI" messaging in the UI itself** (user's call — keep that framing
  out of the product surface; it can still live in README/code comments).

---

## Phase A — Implement the approved redesign in the real app

This is the immediate next phase. Work only in `toolkit/index.html` (CSS +
markup + JS) and `toolkit/server.py` only if a genuinely new capability is
needed (mostly it isn't — this is a frontend restyle of existing features).

### A1. Collapse the toolbar
**Current state (index.html ~line 108-121):** two toolbar rows. Row 1 has 7
buttons: Add files, Add folder, Remove, Clear, Preview scopes, Check banding,
Watch folder, Last report, Settings — all styled identically, all always
visible. Row 2 is the Presets row.
**Target (per mockup):** Row 1 becomes: a split-button **Add** (primary click
= add files; small chevron = dropdown with "Add files…" / "Add folder…"), a
spacer, an icon-only **⚙ Settings** button, and an icon-only **⋯ More** button
opening a small dropdown menu with: Watch folder…, Last report…, What this
does… (Help).
- Remove/Clear come OUT of the global toolbar (see A2 — they move to
  contextual, selection-driven controls).
- Preview scopes / Check banding come OUT of the global toolbar (see A2 — they
  become per-row hover actions).
- Reuse existing JS functions as-is: `pick()`, `openSettings()`,
  `toggleWatchPanel()`, `openReport()`, the Help modal trigger. Only the
  *markup/wiring* changes, not the underlying logic.
- New: a tiny dropdown-menu component (see mockup `.menu-wrap`/`.menu` CSS —
  copy directly, it's self-contained) for the Add split-button chevron and the
  ⋯ More button.

### A2. Contextual queue actions
**Current state:** Compare/Reveal/why? buttons *already exist* per-row (lines
432-433) shown conditionally on status — this part is already right,
structurally. What's missing: Scopes and Check-banding are still
toolbar-global, operating on "selected or first queued file" (see
`openScopes()`/`checkBanding()` — they do
`const target = sel.length? queue[sel[0]] : queue[0]`).
**Target:** Add "Preview scopes" and "Check banding" as additional per-row
hover-action buttons (icon-only, matching Compare/Reveal styling) on **every**
row regardless of status (not just Done) — since you can preview/check-band a
file before converting it. Update `openScopes(i)`/`checkBanding(i)` to take an
explicit row index instead of relying on checkbox selection; keep the
checkbox-selection fallback only for bulk actions (Remove-selected).
- Remove appears only when ≥1 row is checked (a small contextual bar or just
  enable/disable the existing Remove concept — simplest: keep a single
  "Remove" that appears above the queue only when something's checked,
  matching the mockup's restraint).
- Clear (whole queue) can live in the ⋯ More menu instead of the toolbar,
  since it's rare and destructive-ish.

### A3. Presets-first, granular controls hidden by default
**Current state:** Format / Deband / Bitrate are three always-visible
segmented-button rows (below Presets).
**Target:** Wrap them in a `<details class="adjust">` disclosure (see mockup
CSS `.adjust`/`.adjust-body`/`.field-row` — copy directly), closed by default,
with a one-line summary showing the current picks (e.g. "— HEVC · Medium
deband · Match source"). Clicking it expands to the exact same segmented
controls that exist today — no logic change, just wrapped in `<details>` and
initially collapsed. The summary line must update live as the user changes
Format/Deband/Bitrate (hook into the existing `segInit()` callbacks).

### A4. Queue as hero
Widen the app's max-width (or go fluid) so the queue table gets more room;
increase its min-height so it doesn't feel like an afterthought under four
rows of controls.

### A5. One icon language
Replace ALL emoji in `index.html` with the inline-SVG line icons from the
mockup (`.icon-btn svg`, row action icons — copy the exact `<svg>` markup used
for Add/Settings/More/Scopes/Banding/Compare/Reveal/Remove from
`design/approved_mockup_v1.html`). Emoji locations to replace: line 107 (🎬 in
`<h1>`), 117-121 (📊🔍👁📋⚙), line 645 (👁 in `toggleWatchPanel` JS string).
Also check `checkBanding()`'s banner and any other stray emoji (🔍 in the
banner text, etc.) — grep the file for emoji before considering this done.

### A6. Contextual action bar
**Current state:** Convert + "Stop after current" + Cancel are all always
rendered, disabled/enabled via `setControls()`.
**Target (visual only):** Keep the same three buttons/logic, but restyle so
only Convert is prominent when idle; Stop-after/Cancel visually take Convert's
place while running (per mockup State 2). This might just be a CSS/opacity
change rather than DOM add/remove, to keep `setControls()` simple.

### A7. Visual system pass
Adopt the mockup's token system directly — it's a drop-in improvement over the
current ad hoc colors:
- Copy the entire `:root` CSS variable block from the mockup (colors, font
  stacks) into `index.html`'s existing `<style>`, replacing the current
  `--bg/--card/--ink/...` tokens. Re-point every existing CSS rule at the new
  token names (mostly 1:1 renames — check the mockup's names against
  `index.html`'s current ones and reconcile, since they diverged slightly
  during the mockup's own evolution).
- Copy `.pill`, `.chip`, `.qrow`, `.seg`, `.btn*` styles from the mockup where
  they're strictly better (rounded pill presets, tighter chip badges, tabular
  mono numerics via `--mono`).

### A8. Teaching empty state
**New.** When `queue.length === 0`, render the mockup's `.teach` block instead
of (or in addition to) the current plain-text empty message: the 8-bit→10-bit
gradient swatch illustration + "Drag a clip here, or click Add" + the one-line
description + the trust line ("100% local — nothing ever leaves your Mac").
Copy `.teach`/`.teach-illust`/`.swatch` CSS directly from the mockup (the
banded/smooth gradients are pure CSS `repeating-linear-gradient`/
`linear-gradient`, no images needed).

### A9. Quick-look hover preview
**New — needs both frontend and a small backend piece.** On hover over a
**Done** row's filename, show a small popover with two thumbnails (before/
after, same frame) — like the mockup's `.quicklook`.
- Backend: reuse the existing `render_compare()` machinery (already produces
  before/after frame PNGs — see `/api/compare` and `/api/compare-image` in
  `server.py`, used today by the Compare modal). On hover, call `/api/compare`
  with a default timestamp (or cache the result from the last Compare open, if
  any, to avoid re-rendering on every hover) and show the two images in a
  small floating panel instead of opening the full modal.
- Debounce: don't fire the ffmpeg render on every mouse-in — wait ~300ms after
  hover starts, and cancel if the mouse leaves first (mirrors how
  `checkBanding`/scopes already avoid redundant calls).
- Frontend: CSS is in the mockup (`.quicklook`, `.thumb`, `.pane`) — needs real
  `<img>` src wiring instead of the mockup's placeholder gradient divs.

### A10. Named phases, not just percentages
**New — frontend only, data already exists.** The `/api/status` response
already distinguishes phases server-side (e.g. "pass 1/2 (analyzing)" vs the
main encode — see `run_batch()`'s `JOB.now["file"]` strings in `server.py`,
and the dual-export "— HEVC preview" suffix). Currently the frontend just
displays that whole string as-is in the Now-running panel and doesn't surface
a phase on the row itself.
- Parse a short phase label out of `JOB.now`/row state client-side (e.g.
  "Encoding", "Analyzing", "Rendering preview") and render it as the mockup's
  `.phase-pill` next to the filename in both the running queue row and the
  Now-running panel.
- If the existing status strings aren't clean enough to parse reliably,
  consider adding an explicit `"phase"` key to the `JOB.now` dict server-side
  instead of overloading the free-text `"file"` string — this is the more
  robust fix if A10 gets fiddly.

### A11. Inline dismissible batch-summary banner
**New — frontend only, data already exists.** `/api/report` (built in v25)
already returns everything needed: done/skipped/failed counts, total in/out
size, elapsed time. Today it's only shown when the user clicks "Last report".
- When a batch's `/api/status` transitions from `running:true` to
  `running:false`, fetch `/api/report` and render the mockup's
  `.batch-summary` banner (green, checkmark icon, "N done · X MB → Y MB · Zs",
  a "View report" link that opens the existing report modal, and a dismiss ✕).
  Auto-fade/dismiss is optional — the mockup has a manual ✕ only, keep it
  simple.
- Don't show it for a batch of 0 real conversions (e.g. all skipped) unless
  that's still useful signal — use judgement here, or just always show it
  when a batch completes.

### A12. Real app icon
- Add `<link rel="icon">`/favicon using the same 64×64 base64 PNG approach
  proven in the mockup (extract the exact data URI from
  `design/approved_mockup_v1.html`'s `.app-icon` `<img src="data:image/png..."`
  — don't regenerate, reuse the same bytes for consistency).
  `sips -z 64 64 "Imagine Art - Icon.png" --out toolkit/icon64.png` is the
  regeneration command if needed; then base64-encode.
- This is also the source asset for the later `.icns` app-bundle icon
  (Phase D2) — keep the original 534×534 PNG as the master, don't work from
  the downscaled copy for that.

### A13. Verification (don't skip)
Once A1–A12 land, re-run the same verification discipline used throughout this
project: start `server.py`, drive the real page via the claude-in-chrome
browser tools (not just visual screenshots — click through Add/Settings/More,
trigger a real conversion, confirm the quick-look preview actually renders a
real frame, confirm the batch-summary banner appears after a genuine batch
completes, check light AND dark mode). Do not consider Phase A done on
mockup-fidelity alone — the mockup was static HTML; the real app has real
state and real async timing that can break things the mockup couldn't reveal.

**Effort:** ~1–2 focused sessions (A9/A10/A11 are the only pieces with new
logic; A1–A8/A12 are mostly copy-the-mockup's-CSS-and-rewire-existing-JS).
**Risk:** low — every underlying capability already exists and works; this is
almost entirely presentational.

---

## Phase 0 — De-risk spike for going native (do FIRST, ~30 min, before Phase B)
- [ ] `pip install --user pywebview`; a 20-line throwaway script that opens a
  native window pointing at a local page. Confirm it launches, renders HTML,
  and closes cleanly on this Mac's system Python 3.9 (pulls in pyobjc).
- [ ] Confirm WKWebView (Safari engine, not Chrome) renders the *redesigned*
  `index.html` correctly — segmented buttons, modals, drag-drop, sliders,
  the new quick-look popover, the `<details>` disclosure. It was only ever
  tested in Chrome via claude-in-chrome.
- **Gate:** if PyWebView won't cooperate on system Python, the fallback is
  installing a python.org Python inside the `.app` bundle (Phase D handles
  bundling Python anyway) — but confirm this before planning further around it.

## Phase B — PyWebView native shell
- [ ] **B1.** Add `pywebview` to `Start_Here.command`'s install step, with a
  graceful fallback to opening a browser tab if it's missing.
- [ ] **B2.** `server.py` already binds the first free port (see `main()`).
  Instead of always `subprocess.Popen(["open", url])`, when pywebview is
  importable do `webview.create_window("8-bit → 10-bit Converter", url, ...)`
  then `webview.start()`; keep `open()` as the fallback path.
- [ ] **B3.** Closing the native window must cleanly shut the server down
  (hook into pywebview's window-closed event → call the same shutdown path
  `atexit`/`cleanup_temp()` already uses).
- [ ] **B4.** Window config: title (use the same title as A12/the mockup —
  "8-bit → 10-bit Converter by Jazib Ali 360"), sensible min size.
- [ ] **B5.** Keep the existing osascript file/folder pickers for now — note
  `webview.create_file_dialog` as a future native-integration swap, not
  required for B to ship.
- **DoD:** double-clicking `Start_Here.command` opens a native titled window
  (no browser tab, no "keep this Terminal open" messaging needed); every
  feature works in WKWebView; a real conversion completes end-to-end.
- **Effort:** ~half a session after the Phase 0 spike passes.
- **Risk:** medium — WKWebView vs Chrome rendering gaps, mitigated by Phase
  0's check and by the fact that Phase A's CSS avoids anything exotic.

## Phase C — Native polish
- [ ] **C1. Menu bar.** File (Add…, Quit) / Edit / Help via pywebview's menu
  API.
- [ ] **C2. Persistence.** Remember window size + last-used Format/Deband/
  Bitrate picks + last-used add-file folder (this subsumes the "persist
  last-used picks" item from ROADMAP.md's still-open list — store in the same
  settings JSON `server.py` already reads/writes).
- [ ] **C3. First-run card.** A one-time dismissible "what this does" card
  (can literally reuse the existing Help modal content, just auto-shown once).
- *(Keyboard shortcuts — explicitly not wanted, per prior decision. Don't
  add them.)*
- **Effort:** ~1 session. **Risk:** low.

## Phase D — Packaging & distribution
- [ ] **D1.** Build a real `.app`. Bundle Python + pywebview + `server.py` +
  `index.html` + `toolkit/bin/arm64` (the ffmpeg/ffprobe binaries) into
  `10-bit Converter.app`. Spike PyInstaller vs py2app first — PyInstaller
  tends to be more reliable with the system Python; py2app is more
  "Mac-proper." Pick one based on that spike, don't guess upfront.
- [ ] **D2. App icon (`.icns`).** Generate from the master
  `Imagine Art - Icon.png` (534×534) using `iconutil`/`sips` to build the full
  macOS icon size set, then `iconutil -c icns`.
- [ ] **D3. Gatekeeper.** Self de-quarantine on first run where possible
  (same `xattr -dr com.apple.quarantine` pattern already used for the bundled
  ffmpeg binaries in `ensure_bundled_ffmpeg()`); document the one-time
  right-click → Open. Frictionless (no right-click at all) needs an Apple
  Developer account ($99/yr) for signing + notarization — optional, not
  something to build around now.
- [ ] **D4. Release build.** Extend `build_release.sh` to emit the `.app`
  (optionally a `.dmg`) instead of / alongside the current zip.
- [ ] **D5. Docs.** Update `toolkit/README.md` for the app-launch flow instead
  of the "open your browser" instructions.
- **DoD:** a clean macOS account, double-clicking the app (after the one-time
  Gatekeeper step) launches straight into the converter; ffmpeg resolves from
  inside the bundle; no Python/pip visible to the user at all.
- **Effort:** ~1–2 sessions — packaging is the fiddliest part of this whole
  arc. **Risk:** medium-high, bundling the system Python is the classic pain
  point; Phase 0 + D1's own spike de-risk it in advance. Expect bundle size
  ~150–250 MB (Python + ffmpeg) — acceptable and already the norm for this
  category of tool.

---

## Small leftover items (not phase-specific, cheap, do whenever convenient)
- **Output filename preview** before converting — show the computed
  `NAME_10bit.ext` path inline near each queued row or in the Adjust summary,
  using the same `make_output_path()` logic `server.py` already has (would
  need a lightweight endpoint or client-side mirror of the naming rule:
  `{base}{suffix}.{mov|mp4}`).
- **Version number shown in the app** — a small `vN` string in the footer or
  Help modal, bumped alongside each `git tag`.

## Out of scope (still, unchanged)
- AI anything (debanding, deflicker, upscaling). Sharpening/upscaling/editing.
  Cloud/upload. Intel (x86_64) bundle. Electron (too heavy). A from-scratch
  SwiftUI rewrite (throws away the shared web codebase). Keyboard shortcuts.

## Suggested order when picking this back up
1. **Phase A** end-to-end (implement + verify in-browser) — this alone is a
   complete, shippable improvement even if native packaging never happens.
2. **Phase 0 spike** — quick, tells you whether B–D are even worth planning
   around further.
3. **Phase B** — native shell, biggest "feels real" jump for the least work.
4. **Phase C**, then **Phase D** — polish, then package for handing to people.
5. Small leftover items whenever there's a spare 20 minutes.
