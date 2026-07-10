#!/usr/bin/env bash
#
# 8bit_to_10bit.command
# Mac double-click / drag-and-drop tool.
#
# HOW TO USE:
#   1. Double-click this file once to let macOS register it (you may need to
#      right-click -> Open the first time, since it's an unsigned script).
#   2. After that, just DRAG a video file (.mp4/.mov) and DROP it onto this
#      file's icon in Finder. It will convert it and save the result next
#      to the original, then open the folder for you.
#
# Requires ffmpeg. If you don't have it, this script will tell you how to
# install it via Homebrew.

set -euo pipefail
cd "$(dirname "$0")"

MODE="hevc"   # change to "prores" if you want ProRes 4444 10-bit for grading

# --- Check ffmpeg ---
if ! command -v ffmpeg >/dev/null 2>&1; then
  osascript -e 'display alert "ffmpeg not found" message "Install it by opening Terminal and running:\n\nbrew install ffmpeg\n\n(If you dont have Homebrew: install it first from brew.sh)"'
  exit 1
fi

# --- Get input file (dropped onto the app, or opened via dialog) ---
if [[ $# -ge 1 ]]; then
  INPUT="$1"
else
  INPUT=$(osascript -e 'POSIX path of (choose file with prompt "Select a video to convert to 10-bit")')
fi

if [[ ! -f "$INPUT" ]]; then
  osascript -e 'display alert "File not found" message "Could not read the dropped/selected file."'
  exit 1
fi

DIR=$(dirname "$INPUT")
BASE=$(basename "$INPUT")
NAME="${BASE%.*}"

if [[ "$MODE" == "prores" ]]; then
  OUTPUT="$DIR/${NAME}_10bit.mov"
else
  OUTPUT="$DIR/${NAME}_10bit.mp4"
fi

echo "Converting: $INPUT"
echo "Output:     $OUTPUT"
echo "Mode:       $MODE"
echo

FILTERS="deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv420p10le"

if [[ "$MODE" == "prores" ]]; then
  ffmpeg -y -i "$INPUT" \
    -vf "$FILTERS" \
    -c:v prores_ks -profile:v 4444 -pix_fmt yuv444p10le \
    -c:a pcm_s16le \
    "$OUTPUT"
else
  ffmpeg -y -i "$INPUT" \
    -vf "$FILTERS" \
    -c:v libx265 -pix_fmt yuv420p10le -crf 18 -preset slow \
    -tag:v hvc1 \
    -c:a aac -b:a 192k \
    "$OUTPUT"
fi

# --- Notify + reveal in Finder ---
osascript -e "display notification \"Saved to $(basename "$OUTPUT")\" with title \"10-bit conversion done\""
open -R "$OUTPUT"
