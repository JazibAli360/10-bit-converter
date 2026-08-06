# Redesign & Native-App Plan

This is the current implementation plan for turning the completed browser
interface into a distributable, Apple-Silicon-native macOS app. It is written
to be safe to resume in a future session.

## Product direction

- Preserve the existing conversion engine and web UI: the native app wraps
  `toolkit/server.py` and `toolkit/index.html`; no second UI codebase and no
  SwiftUI rewrite.
- Keep the product local, private, Apple Silicon-only, and quality-first.
  There is no AI, cloud processing, or hardware/fast-encode mode.
- Ship a real `.app`: users should not need a browser, Terminal, Python,
  Homebrew, or `pip`.
- Built by **Jazib Ali 360**.

## Where things stand

- The approved mockup is `design/approved_mockup_v1.html`.
- **The web redesign is complete and verified in the real app.** It includes
  the collapsed toolbar, contextual queue actions, presets-first Adjust
  disclosure, hero queue, SVG icon language, teaching empty state, quick-look
  preview, named progress phases, batch-completion banner, embedded favicon,
  and light/dark-mode verification with real conversions.
- The corresponding commits are `fb60510` through `d4b45ba`.
- The next work is the frozen native-app vertical slice described in Phase 0.
- `toolkit/JZB.png`, `toolkit/JZB.icns`, and `toolkit/10bit_converter.icns`
  are the shipped product assets. Keep release visual work based on those
  assets; do not reintroduce unrelated source icons into the public project.

## Completed redesign record (Phase A)

Phase A is intentionally archived here rather than left as future work.

- A1: collapsed Add split button, Settings, and More menu.
- A2: contextual selection controls; per-row Scopes and Band actions.
- A3–A7: presets-first disclosure, larger queue, contextual conversion
  controls, unified tokens/icons, and tabular numeric display.
- A8–A12: teaching empty state, debounced quick-look preview, named phases,
  batch summary, and embedded icon.
- A13: verified through a live browser conversion, including quick-look and
  completion summary, in light and dark appearance.

The browser app remains a supported development/fallback mode. Do not redo
this phase unless a specific regression is found.

---

## Phase 0 — Frozen vertical-slice spike (first)

**Goal:** prove the risky part: a packaged app, not merely a PyWebView window.

1. Create an arm64 virtual environment with pinned Python, PyWebView/PyObjC,
   and packaging-tool versions. Do not depend on macOS's system Python or use
   `pip install --user`.
2. Start with **py2app**, which PyWebView documents for macOS. Keep
   PyInstaller as a measured fallback if the actual spike proves py2app
   unsuitable; do not choose based on guesswork.
3. Build a minimal arm64 `.app` that bundles and successfully uses:
   `server.py`, `index.html`, the icon, `ffmpeg`, and `ffprobe`.
4. In the frozen build, start the existing HTTP server on a background thread,
   then run `webview.start()` on the main thread. Verify a normal titled
   WKWebView window opens.
5. Verify a bundled-FFmpeg probe/conversion, a preference write outside the
   app bundle, a clean close, and no remaining listener/process after quit.
6. Test the frozen build in a clean macOS account without Homebrew or a
   developer Python.

**Gate:** continue only once the frozen app launches, renders correctly in
WKWebView, runs its bundled binaries, persists test data, and exits cleanly.
This phase is expected to surface packaging problems early, while the app is
still small.

---

## Phase 1 — Native-readiness refactor

Do this before treating PyWebView as a production shell.

### 1.1 Application data and resource paths

- Keep immutable app resources inside the bundle.
- Move all writable state out of `HERE` and into
  `~/Library/Application Support/Jazib Ali 360/10-bit Converter/`:
  settings, custom presets, conversion log, window state, and first-run state.
- Put disposable preview/cache data in a per-run cache under the system temp
  directory. Delete it on clean exit and prune stale data on launch.
- Use an atomic write/replace for JSON preferences so a forced quit cannot
  leave malformed settings.

### 1.2 Server lifecycle and conversion ownership

- Refactor startup into an explicit application controller:
  bind the loopback server, run `serve_forever()` in a worker thread, create
  the native window, and run the GUI loop on the main thread.
- Implement one idempotent `shutdown()` path: stop new requests, stop the
  watcher, cancel/terminate owned FFmpeg processes when requested, close the
  server, join short-lived threads, then remove temporary files.
- On close during a conversion, ask whether to keep working or cancel and
  quit. Never silently remove a partial output or temporary source while an
  FFmpeg child still owns it.
- Define single-instance behaviour: a second launch should foreground the
  existing app rather than start a competing server/watch process.

### 1.3 Local API and UI hardening

The loopback server remains an API and must not trust every local request.

- Generate a cryptographically random, per-launch API token. Require it on
  every API request, including image/preview endpoints.
- Reject unexpected `Host` and `Origin` values; apply request-size limits and
  return safe errors.
- Restrict file operations to paths selected/queued by the app. Do not allow
  arbitrary local paths in Convert, Reveal, Compare, Scopes, or Settings APIs.
- Escape every filename, path, error, preset name, and report value before it
  reaches `innerHTML`; prefer DOM nodes and `textContent` for user-controlled
  values.
- Add conservative security headers and disable caching for API responses.

### 1.4 Native file acquisition

- Use PyWebView's native file/folder dialog for the packaged app. Retain the
  `osascript` picker only for the browser fallback.
- For native drag-and-drop, queue original file paths through PyWebView's
  dropped-file support. Do **not** upload/copy entire video files into the
  temporary intake directory; large source clips make that unacceptable.

### 1.5 Accessibility and native fit

- Keep normal macOS window chrome; avoid a frameless imitation title bar.
  Make the in-page header compact enough that it does not duplicate the native
  title bar.
- Give every icon control an accessible label, a visible focus style, and
  keyboard-operable menu/modal behaviour. Show row actions on focus-within as
  well as hover.
- Check contrast, reduced motion, VoiceOver labels/focus order, and light/dark
  mode. These are accessibility requirements, not custom conversion shortcuts.
- Standard macOS commands such as Quit and Preferences may remain in the menu
  bar; product-specific keyboard shortcuts stay out of scope.

**Definition of done:** the browser fallback still works; the native-safe
backend has app-data paths, bounded temp storage, a secure loopback API,
safe text rendering, path-based native drag-and-drop, and a deterministic
shutdown path.

---

## Phase 2 — PyWebView native shell

1. Add a normal titled window named `8-bit → 10-bit Converter by Jazib Ali 360`
   with a sensible minimum size and native app icon.
2. Use the Phase 1 application controller to run the server and GUI loop.
3. Wire native file/folder dialogs, drag-and-drop paths, Reveal in Finder, and
   conversion start/stop into the existing UI.
4. Add native menus: application/About, File (Add files, Add folder, Quit),
   Edit, and Help. Ensure they call the same product actions as the UI.
5. Persist window size/position, last-used Format/Deband/Bitrate, last picker
   directory, and one-time onboarding state in Application Support.
6. Show a one-time dismissible first-run card using the existing Help content.

**Definition of done:** opening the app shows a native window with no browser
or Terminal; native dialogs and drag/drop work; a genuine conversion,
comparison, scopes render, watch folder, report, cancel, and graceful stop all
work in WKWebView.

---

## Phase 3 — Packaging and release

### 3.1 Bundle construction

- Build an arm64-only `.app` with Python, PyWebView/PyObjC, web resources, and
  the arm64 FFmpeg/FFprobe binaries.
- Use a dedicated bundle-resource resolver; never rely on the launch working
  directory.
- Centralize the version so it feeds the app UI, `CFBundleShortVersionString`,
  `CFBundleVersion`, release archive name, and git tag.
- Generate and verify a full `.icns` from the 1024x1024 master image.

### 3.2 Gatekeeper and signing tiers

- Development/unsigned builds: document the one-time right-click → Open path.
- Do not promise that the app can self-remove quarantine before Gatekeeper has
  allowed it to launch.
- Public releases: sign every nested executable with a Developer ID identity,
  enable hardened runtime, notarize with `notarytool`, staple the ticket, and
  verify the finished artifact. This is the route to frictionless launch.

### 3.3 FFmpeg attribution and license compliance

- The bundled FFmpeg build enables GPL components. Before any public release,
  add a Third-Party Notices screen/file, preserve the exact build configuration,
  and provide the corresponding FFmpeg and dependency source/notice material.
- Review distribution obligations and codec-patent exposure before selling or
  broadly distributing the app.

### 3.4 Release automation and docs

- Extend `build_release.sh` (or replace it with a dedicated release script) to
  build, sign when credentials are present, package as ZIP/DMG, and emit
  checksums.
- Update `toolkit/README.md` for app launch, Gatekeeper tier, privacy, bundled
  dependencies, troubleshooting, and browser fallback.

**Definition of done:** an arm64 clean macOS account can open the packaged app,
complete a conversion using bundled FFmpeg, retain settings across relaunch,
and do so without Python, pip, a browser, or Terminal.

---

## Phase 4 — Release-quality verification

Run the following on the packaged artifact, not only from the repository:

- Clean user account; no Homebrew/developer Python.
- Light and dark appearance; small and large window sizes; VoiceOver/focus
  navigation; reduced-motion setting.
- File/folder dialog, native drag-and-drop, external drive, Unicode/long file
  names, permissions failure, nonexistent/moved source, and multiple videos.
- A very large file: verify native drag/drop does not make a full temporary
  copy.
- Low free space, conversion failure, cancel, Stop after current, app close
  during conversion, watch-folder activity, sleep/wake, and a second launch.
- HEVC, ProRes, scopes, banding meter, compare, quick-look, custom presets,
  output collision handling, report, and Finder reveal.
- Verify the final app signature/notarization status when using the signed
  release tier.

---

## Small follow-ups

- Output filename preview before conversion, derived from the server's output
  path logic rather than duplicated fragile client-side rules.
- App version in Help/footer, driven by the centralized release version.
- Consider bounded/evictable quick-look caching so repeated hover previews do
  not accumulate FFmpeg-rendered frame directories during a long session.
- Keep **Faithful 10-bit** on the current FFmpeg deband+dither pipeline. An
  optional deterministic libplacebo backend is specified, gated, and scoped in
  `toolkit/IMPLEMENTATION_ROADMAP.md`; do not begin it until its M3 build spike
  passes.

## Out of scope

- AI processing, cloud/upload, sharpening/upscaling/editing, Intel support,
  Electron, and a from-scratch SwiftUI rewrite.
- Custom conversion keyboard shortcuts.

## Reference decisions

- PyWebView uses WKWebView on macOS and requires its GUI loop on the main
  thread. Its documentation recommends py2app for macOS freezing and advises
  CSRF protection when an external local server is used.
- Apple notarization requires Developer ID signing and hardened runtime.
- FFmpeg's own licensing guidance applies because the distributed binary has
  GPL components enabled.
