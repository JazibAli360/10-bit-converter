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

- **One-click setup + launch** — `Start_Here.command` installs dependencies
  and opens the app for you.
- **Batch / folder processing** — point any tool at a folder (or drop/select
  multiple files) and it converts them all, writing `NAME_10bit.EXT` next to
  each source.
- **Real progress %** — the GUI shows a true percentage bar and the scripts
  print live percentage, both parsed from ffmpeg (no more fake spinner).
- **Deband strength control** — Low / Medium / High, so you can match the
  amount of debanding to how bad the banding actually is.
- **Cancel button (GUI)** — stop a running conversion/batch instantly; the
  in-progress output file is cleaned up so nothing broken is left behind.

## Files in this toolkit

### `Start_Here.command`  ← start here
Double-click to install everything needed and launch the GUI. See Quick start
above.

### `10bit_converter_gui.py`
The GUI. A queue you can fill with **multiple files or a whole folder**, a
format dropdown (HEVC vs ProRes 4444), a **deband-strength dropdown**
(Low/Medium/High), a **real percentage progress bar**, and a **Cancel button**
that stops a running batch immediately (the partial output is discarded). Run
directly with:
```bash
python3 10bit_converter_gui.py
```
(Normally you don't need this — `Start_Here.command` launches it for you.)

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

```bash
brew install ffmpeg python-tk
```
Optional, for real drag-and-drop in the GUI:
```bash
pip3 install tkinterdnd2
```

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
