# Redesign & Native-App Plan

Plan of action to (1) declutter/modernize the UI and (2) turn the web app into a
real native macOS app. Internal planning doc; not shipped in the release zip.

## Why
The UI grew feature-by-feature into ~14 equal-weight controls in the resting
state (10 toolbar buttons across two rows + 4 rows of segmented controls +
emoji icons). It works but reads like a form, not a modern app. Separately,
running in a browser tab with a "keep this Terminal open" server is functional
but doesn't feel like a product.

## Principles (unchanged)
- No AI / no reinterpretation of detail. Quality over speed. Local, private,
  zero-install. Apple Silicon (arm64). Built by **Jazib Ali 360**.
- **New:** progressive disclosure — show the ~5 things everyone uses; everything
  else lives behind hierarchy (menus, expanders, per-row actions).
- **Reuse, don't rewrite.** The native app wraps the *existing* `server.py` +
  `index.html`; no second codebase.

---

## Phase 0 — De-risk spike (do FIRST, ~30 min)
Before planning around PyWebView, confirm it actually runs on this machine's
Python 3.9.
- [ ] `pip install --user pywebview`; a 20-line script that opens a native
  window pointing at a throwaway local page. Confirm it launches, renders HTML,
  and closes cleanly on this system Python (pulls pyobjc).
- [ ] Confirm WebKit renders `index.html` correctly (it was only ever tested in
  Chrome; WKWebView is Safari-engine — check the segmented buttons, modals,
  drag-drop, sliders).
- **Gate:** if PyWebView won't cooperate on system Python, fall back to
  "install a python.org Python in the .app bundle" (Phase D handles Python
  anyway) — but we want to know now.

---

## Phase A — UX declutter (frontend only: `index.html`)
Ships independently of the native shell and improves things immediately.

- [ ] **A1. Collapse the toolbar to 3 items.** Primary **＋ Add** (split button:
  files / folder), **Settings**, and a **⋯ More** menu holding Watch folder,
  Last report, Help. Remove/Clear leave the global bar.
- [ ] **A2. Contextual queue actions.** Remove appears only when rows are
  checked; per-row hover reveals Scopes / Compare / Reveal / Remove. This kills
  the "select a file first" nag for the analysis tools (they're per-file).
- [ ] **A3. Presets-first, granular-hidden.** Keep Delivery / Grading / Max
  preservation + custom presets prominent; fold Format / Deband / Bitrate into a
  single collapsible **"Adjust ▸"** section (remembers open/closed). Removes 3
  rows from the default view.
- [ ] **A4. Queue as hero.** Wider max-width (or fluid), a real drag-drop
  empty-state zone, queue fills available height.
- [ ] **A5. One icon language.** Replace emoji with a small inline-SVG line-icon
  set (self-contained — no CDN, stays offline/CSP-safe). Consistent 20px icons.
- [ ] **A6. Contextual action bar.** Convert always visible; Stop-after /
  Cancel appear only while a batch runs (occupying the same spot).
- [ ] **A7. Visual system pass.** Consistent spacing scale, fewer hairline
  borders, tighter type scale, one accent, proper hover/focus states, honest
  empty/idle states. Keep light/dark.
- **Definition of done:** resting state ≤ ~5 visible controls; every existing
  feature still reachable in ≤ 2 clicks; verified via browser screenshots in
  light + dark, and a full convert still works.
- **Effort:** ~1 focused session. **Risk:** low (pure frontend, easily reverted
  per-commit).

---

## Phase B — PyWebView native shell
Wrap the existing app in a native window. No frontend rewrite.

- [ ] **B1. Dependency.** Add `pywebview` to `Start_Here.command`'s install step
  (with graceful fallback to opening a browser tab if it's missing).
- [ ] **B2. Launch path.** `server.py` already binds a free port; instead of
  `subprocess.Popen(["open", url])`, when pywebview is available do
  `webview.create_window("8-bit → 10-bit Converter", url, width=…, height=…)`
  then `webview.start()`. Browser `open()` stays as the fallback.
- [ ] **B3. Lifecycle.** Closing the window shuts the server down cleanly
  (atexit already clears temp; wire window-closed → server shutdown).
- [ ] **B4. Window config.** Title, sensible min size, restore last size
  (ties into C3).
- [ ] **B5. File dialogs.** Keep the working osascript pickers for now; note
  `webview.create_file_dialog` as a later native-integration option.
- **Definition of done:** double-clicking Start_Here opens a native titled
  window (no browser tab, no lingering visible Terminal expectation); every
  feature works in WKWebView; a real conversion completes.
- **Effort:** ~half a session after the spike. **Risk:** medium — WKWebView vs
  Chrome rendering differences (mitigated by A-phase testing + spike).

---

## Phase C — Native polish
- [ ] **C1. Menu bar.** File (Add…, Quit) / Edit / Help via pywebview's menu API
  — a strong "real app" signal for little cost.
- [ ] **C2. Keyboard shortcuts.** ⌘O add, ⌘↵ convert, Space = preview selected,
  ⌦ remove selected (JS keydown → existing functions).
- [ ] **C3. Persistence.** Remember window size + last-used Format/Deband/Bitrate
  picks + last-used folder (open roadmap item; store in the settings JSON).
- [ ] **C4. First-run card.** One-time honest "what this does / doesn't do,"
  dismissable, then out of the way.
- **Effort:** ~1 session. **Risk:** low.

---

## Phase D — Packaging & distribution
- [ ] **D1. Build a real `.app`.** Bundle Python + pywebview + `server.py` +
  `index.html` + `bin/arm64` ffmpeg into `10-bit Converter.app`. Evaluate
  **PyInstaller vs py2app** (PyInstaller tends to be more reliable with the
  system Python; py2app is more "Mac-proper"). Pick one in a spike.
- [ ] **D2. App icon** (`.icns`).
- [ ] **D3. Gatekeeper.** Self de-quarantine on first run where possible;
  document the one-time right-click → Open. **Frictionless = Apple Developer
  account ($99/yr)** for signing + notarization — optional, out of our hands.
- [ ] **D4. Release build.** Extend `build_release.sh` to emit the `.app` (and
  optionally a `.dmg`) instead of / alongside the current zip.
- [ ] **D5. Docs.** Update README + first-run notes for the app flow.
- **Definition of done:** a clean-account double-click launches the app (after
  the one-time Gatekeeper step); ffmpeg resolves from inside the bundle.
- **Effort:** ~1–2 sessions (packaging is fiddly). **Risk:** medium-high —
  bundling the system Python is the classic pain point; the Phase 0/D1 spikes
  de-risk it. Bundle size ~150–200 MB (Python + ffmpeg) — acceptable.

---

## Recommended sequence
**0 → A → B → C → D.** Phase A delivers value on its own and can ship even if we
never do the native shell. Phase 0 gates B–D. Each phase is independently
committable/revertible (per the version-control approach).

## Out of scope (still)
- AI anything; sharpening/upscaling/editing; cloud/upload; Intel bundle;
  Electron (too heavy); a from-scratch SwiftUI rewrite (loses the shared web
  codebase).

## Open questions for you
1. Ship **Phase A on its own first** (see the declutter live), then decide on
   the native shell? Or commit to the whole arc now?
2. **Mock the new layout first** (rendered preview to approve) before I touch
   the real `index.html`?
3. Native **menu bar + keyboard shortcuts** — worth it to you, or is a clean
   window enough?
