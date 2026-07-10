# 8-bit → 10-bit Video Conversion Toolkit

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

## Files in this toolkit

### `8bit_to_10bit.sh`
Command-line ffmpeg script. Run from Terminal:
```bash
./8bit_to_10bit.sh input.mp4                      # HEVC 10-bit (default)
./8bit_to_10bit.sh input.mp4 output.mov prores     # ProRes 4444 10-bit
```

### `8bit_to_10bit.command`
Same logic, but double-click/drag-and-drop friendly on Mac. Drop a video
file onto its icon in Finder to convert it. First run: right-click → Open
(macOS blocks unsigned scripts by default).

### `10bit_converter_gui.py`
A GUI version with buttons, a format dropdown (HEVC vs ProRes 4444), and a
progress bar. Run with:
```bash
python3 10bit_converter_gui.py
```

## One-time setup (all tools need this)

```bash
brew install ffmpeg python-tk
```

Optional, for real drag-and-drop in the GUI version:
```bash
pip3 install tkinterdnd2
```

## Tuning

All three tools use the same filter chain:
```
deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv420p10le
```

If a scene has heavy banding (smooth skies, gradient backgrounds), bump the
`1thr`/`2thr`/`3thr` values up to `0.04–0.06` for a stronger deband effect.
Too high starts to soften real detail, so nudge gradually and eyeball it.
