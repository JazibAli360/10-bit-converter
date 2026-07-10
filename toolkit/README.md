# 8-bit → 10-bit Video Conversion Toolkit

## Quick start (easiest — just one file)

**Double-click `Start_Here.command`.**

It checks what your Mac has, offers to install anything missing (Homebrew,
ffmpeg, Python + Tk, optional drag-and-drop), and then opens the converter
app. You just confirm the prompts.

> First run only: macOS blocks unsigned scripts, so if double-click does
> nothing, **right-click `Start_Here.command` → Open → Open**. You do this
> once, then double-click works normally.

That's all most people need. The rest of this README is for the individual
tools and for command-line use.

## What this actually does (read this first)

Converting 8-bit AI-generated footage to 10-bit does **not** recover color
information that was never generated. If Seedance/Kling/Runway rendered a
scene in 8-bit, there's no hidden 10-bit data sitting underneath to unlock.

What this toolkit actually does:

- **Kills banding** — visible stepping in gradients (skies, smoke,
  translucent plastic, skin tones) via a deband + dither filter pass. This
  is a real, visible fix *if* your footage has visible banding.
- **Adds grading headroom** — if you're pushing curves/saturation further
  in DaVinci or Premiere afterward, doing that on 8-bit source can create
  *new* banding. A 10-bit intermediate reduces that risk.
- **Adds fine dither/grain** — perceptually reads as smoother/richer to
  the eye, but it's synthetic, not recovered detail.

What it will **not** do: sharpen detail, fix flat/plasticky lighting, or
improve texture. If footage looks flat rather than banded, that's a
generation-quality issue (prompting, lighting, contrast) — fix it at the
prompt level, not with a bit-depth conversion.

## What's new in this version

- **Bundled ffmpeg — zero install** — a static `ffmpeg`/`ffprobe` for Apple
  Silicon ships inside `bin/arm64/`. Every tool prefers it, so there's nothing
  to install and no network needed. (Intel Macs fall back to a system/Homebrew
  ffmpeg; `Start_Here` offers to install it.)
- **One-click setup + launch** — `Start_Here.command` installs dependencies
  and opens the app for you.
- **Batch / folder processing** — point any tool at a folder (or drop/select
  multiple files) and it converts them all, writing `NAME_10bit.EXT` next to
  each source.
- **Real progress %** — the GUI shows a true percentage bar and the scripts
  print live percentage, both parsed from ffmpeg (no more fake spinner).
- **Deband strength control** — Low / Medium / High, so you can match the
  amount of debanding to how bad the banding actually is.
- **Scopes preview (GUI)** — before converting, click “Preview scopes” to see
  the selected clip’s frame + histogram, source vs processed, side by side.
  Banding shows as a comb (gaps) in the histogram; after debanding it fills in.
  Lets you dial in strength and *see* the effect before a full encode.
- **Cancel button (GUI)** — stop a running conversion/batch instantly; the
  in-progress output file is cleaned up so nothing broken is left behind.
- **Queue table (GUI)** — each file shows its own status (Queued → Running →
  Done / Failed / Skipped / Cancelled) and per-file progress %, active row
  highlighted.
- **Live "Now running" panel (GUI)** — shows the current file with live %,
  frame, fps, encode speed and ETA parsed straight from ffmpeg.
- **Settings (GUI)** — output folder, filename suffix, skip-vs-overwrite,
  HEVC CRF + preset, deband range/blur, dither amount, and a custom deband
  threshold. Saved to disk so your choices persist between launches.

## Files in this toolkit

### `Start_Here.command`  ← start here
Double-click to install everything needed and launch the GUI. See Quick start
above.

### `server.py` + `index.html`  (the app)
The converter runs as a small **local web app** — no GUI toolkit, Python
stdlib only. `Start_Here.command` starts it and opens your browser to
`http://127.0.0.1:8766`. It has a **queue** (each row shows status + live %),
**Format** (HEVC / ProRes 4444), **Deband** (Low/Medium/High/Custom), and
**Bitrate** (Match source / Quality / Custom Mbps) controls, an optional
**Deflicker** pass (Settings), a **live "Now running"** readout (%, frame, fps, speed, ETA), a
**Cancel** button (stops instantly, partial output discarded), a **Settings**
dialog (output folder, suffix, skip/overwrite, CRF, preset, deband range/blur,
dither, custom threshold — persisted), and **Preview scopes** (source vs
processed frame + histogram, so you can see the debanding before a full encode).
Files/folders are chosen with native macOS dialogs. Run directly with:
```bash
python3 server.py
```
> Why a browser instead of a native window? The macOS system Tk (used by
> Python's Tkinter) is deprecated and renders blank on current macOS, so a
> Tkinter GUI is unreliable. A local web app avoids that entirely and needs
> no extra install.

### `8bit_to_10bit.command`
Mac double-click / drag-and-drop tool. Drop **one or more files, or a folder**
onto its icon in Finder. On launch it asks you for the output format and
deband strength, shows live progress in the Terminal window, then reveals the
results in Finder. First run: right-click → Open (macOS blocks unsigned
scripts by default).

### `8bit_to_10bit.sh`
Command-line ffmpeg script with flags. Run from Terminal:
```bash
./8bit_to_10bit.sh input.mp4                          # HEVC 10-bit, medium deband
./8bit_to_10bit.sh -m prores -s high input.mp4 out.mov # ProRes 4444, strong deband
./8bit_to_10bit.sh --mode prores ./my_clips_folder     # batch a whole folder
```
Options:
- `-m, --mode  hevc|prores` — output codec (default `hevc`).
- `-s, --strength  low|med|high` — deband strength (default `med`).
- `-h, --help` — full usage.

## One-time setup (if you're not using Start_Here)

ffmpeg is bundled and the GUI uses standard Tkinter (no extra toolkit), so
usually there's nothing to install. If your Python has no Tk at all:
```bash
brew install python-tk        # Python 3 with Tk
```
Optional drag-and-drop into the GUI:
```bash
pip3 install tkinterdnd2
```
(On Intel Macs with no bundled ffmpeg, also: `brew install ffmpeg`.)

## Deband strength — what the levels mean

All tools build the same filter chain; only the threshold changes with the
strength you pick:

| Strength | Threshold (1/2/3thr) | Use it when… |
|----------|----------------------|--------------|
| Low      | 0.01                 | Mild banding; you want to preserve maximum fine detail. |
| Medium   | 0.02 (default)       | Typical AI footage with some sky/gradient banding. |
| High     | 0.05                 | Heavy banding in smooth skies / gradient backgrounds. Softens real detail slightly, so don't overshoot. |

The underlying chain is:
```
deband=1thr=<T>:2thr=<T>:3thr=<T>:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv420p10le
```
For manual tuning you can edit the threshold directly; nudge gradually and
eyeball it, since too high starts to soften real detail.

## Verifying the output is really 10-bit

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 output.mp4
```
Expect `yuv420p10le` for HEVC. For ProRes 4444 you'll see `yuv444p12le` —
that's expected: ProRes 4444 is a 12-bit format, so it comfortably exceeds
the 10-bit target.
