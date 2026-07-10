#!/usr/bin/env bash
#
# 8bit_to_10bit.sh
# Upconverts 8-bit AI-generated footage (Seedance/Kling/Runway output, etc.)
# to a clean 10-bit deliverable.
#
# IMPORTANT: this does NOT recover color information that was never captured.
# What it does:
#   1. Debands gradients (the actual visible problem in 8-bit AI video —
#      banding in skies, smoke, gradients, skin)
#   2. Adds subtle dithering/noise so the eye perceives smooth gradation
#   3. Re-encodes into a true 10-bit container/codec so downstream grading
#      (DaVinci, Premiere) has more headroom and doesn't reintroduce banding
#
# USAGE:
#   ./8bit_to_10bit.sh input.mp4 [output.mov] [mode]
#
#   mode = "prores"  -> ProRes 4444 10-bit .mov (best for grading, huge file)
#   mode = "hevc"     -> HEVC Main10 .mp4 (smaller, good for delivery/preview)
#   default = hevc
#
# EXAMPLES:
#   ./8bit_to_10bit.sh scene_s5_airplane_wing.mp4
#   ./8bit_to_10bit.sh scene_s5.mp4 scene_s5_10bit.mov prores

set -euo pipefail

INPUT="${1:?Usage: $0 input.mp4 [output] [prores|hevc]}"
MODE="${3:-hevc}"

if [[ "$MODE" == "prores" ]]; then
    OUTPUT="${2:-${INPUT%.*}_10bit.mov}"
else
    OUTPUT="${2:-${INPUT%.*}_10bit.mp4}"
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file '$INPUT' not found." >&2
    exit 1
fi

echo "Input:  $INPUT"
echo "Output: $OUTPUT"
echo "Mode:   $MODE"
echo

# --- Filter chain ---
# deband: removes 8-bit gradient stepping (the real 'lack of depth' culprit)
#   range=16      : sampling radius for the deband algorithm
#   1thr/2thr/3thr: threshold per plane (Y, Cb, Cr) — subtle, avoids mushing detail
# noise: adds barely-visible dither grain so any residual banding is masked
#   perceptually — this is the same trick colorists use before a grade
# format=yuv420p10le: forces true 10-bit 4:2:0 pixel format for the encode
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

echo
echo "Done. Verify bit depth with:"
echo "  ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 \"$OUTPUT\""
