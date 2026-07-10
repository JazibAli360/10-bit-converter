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
#   ./8bit_to_10bit.sh [options] <input> [output]
#
#   <input>   A video file, OR a folder (batch: converts every video in it).
#   [output]  Optional. For a single file only. Ignored in batch/folder mode
#             (outputs are written next to each source as NAME_10bit.EXT).
#
# OPTIONS:
#   -m, --mode      hevc | prores     Output codec (default: hevc)
#                                       hevc   -> HEVC Main10 .mp4 (small, delivery/preview)
#                                       prores -> ProRes 4444 10-bit .mov (grading, huge file)
#   -s, --strength  low | med | high  Deband strength (default: med)
#                                       low  = 0.01  (barely-there, preserves detail)
#                                       med  = 0.02  (balanced default)
#                                       high = 0.05  (smooth skies/gradients, softer)
#   -h, --help                        Show this help.
#
# EXAMPLES:
#   ./8bit_to_10bit.sh scene_s5_airplane_wing.mp4
#   ./8bit_to_10bit.sh -m prores -s high scene_s5.mp4 scene_s5_10bit.mov
#   ./8bit_to_10bit.sh --mode prores ./my_clips_folder      # batch a whole folder

set -euo pipefail

# ---------------------------------------------------------------------------
# Prefer the bundled ffmpeg/ffprobe (toolkit/bin/<arch>), fall back to system.
# No install needed when the bundle is present. Clears Gatekeeper quarantine
# on first run (harmless once cleared / if not quarantined).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLED_BIN="$SCRIPT_DIR/bin/$(uname -m)"
if [[ -x "$BUNDLED_BIN/ffmpeg" && -x "$BUNDLED_BIN/ffprobe" ]]; then
    xattr -dr com.apple.quarantine "$BUNDLED_BIN" 2>/dev/null || true
    export PATH="$BUNDLED_BIN:$PATH"
fi

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MODE="hevc"
STRENGTH="med"
INPUT=""
OUTPUT=""

usage() {
    sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# ---------------------------------------------------------------------------
# Parse args (flags in any order; up to two positionals: input, output)
# ---------------------------------------------------------------------------
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--mode)      MODE="${2:?--mode needs a value}"; shift 2 ;;
        -s|--strength)  STRENGTH="${2:?--strength needs a value}"; shift 2 ;;
        -h|--help)      usage 0 ;;
        --)             shift; while [[ $# -gt 0 ]]; do POSITIONAL+=("$1"); shift; done ;;
        -*)             echo "Unknown option: $1" >&2; usage 1 ;;
        *)              POSITIONAL+=("$1"); shift ;;
    esac
done

INPUT="${POSITIONAL[0]:-}"
OUTPUT="${POSITIONAL[1]:-}"

[[ -z "$INPUT" ]] && { echo "Error: no input given." >&2; usage 1; }

# Validate mode + strength
case "$MODE" in hevc|prores) ;; *) echo "Error: --mode must be hevc or prores." >&2; exit 1 ;; esac

case "$STRENGTH" in
    low)  THR="0.01" ;;
    med)  THR="0.02" ;;
    high) THR="0.05" ;;
    *) echo "Error: --strength must be low, med, or high." >&2; exit 1 ;;
esac

command -v ffmpeg  >/dev/null 2>&1 || { echo "Error: ffmpeg not found. Install with: brew install ffmpeg" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "Error: ffprobe not found. Install with: brew install ffmpeg" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Filter chain (built from chosen strength)
#   deband: removes 8-bit gradient stepping (the real 'lack of depth' culprit)
#     range=16      : sampling radius for the deband algorithm
#     1/2/3thr      : threshold per plane (Y, Cb, Cr) — from --strength
#   noise: barely-visible dither grain so residual banding is masked to the eye
#   format=...10le : forces a true 10-bit pixel format for the encode
# ---------------------------------------------------------------------------
build_filters() {
    local pixfmt="$1"   # yuv420p10le (hevc) or yuv444p10le (prores)
    echo "deband=1thr=${THR}:2thr=${THR}:3thr=${THR}:range=16:blur=1,noise=alls=2:allf=t+u,format=${pixfmt}"
}

# ---------------------------------------------------------------------------
# Convert one file, printing a real progress percentage.
# ---------------------------------------------------------------------------
convert_one() {
    local in="$1" out="$2"

    # Total duration (seconds, float) for percentage math.
    local dur
    dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in" 2>/dev/null || echo "0")
    [[ -z "$dur" || "$dur" == "N/A" ]] && dur="0"

    local filters args
    if [[ "$MODE" == "prores" ]]; then
        filters=$(build_filters yuv444p10le)
        args=(-vf "$filters" -c:v prores_ks -profile:v 4444 -pix_fmt yuv444p10le -c:a pcm_s16le)
    else
        filters=$(build_filters yuv420p10le)
        args=(-vf "$filters" -c:v libx265 -pix_fmt yuv420p10le -crf 18 -preset slow -tag:v hvc1 -c:a aac -b:a 192k)
    fi

    echo "  in:  $in"
    echo "  out: $out"

    # Run ffmpeg emitting machine-readable progress on stdout; parse out_time.
    ffmpeg -y -nostats -i "$in" "${args[@]}" -progress pipe:1 "$out" 2>/dev/null \
    | while IFS='=' read -r key val; do
        if [[ "$key" == "out_time_us" || "$key" == "out_time_ms" ]]; then
            # out_time_ms is historically microseconds in ffmpeg; both are µs here.
            [[ "$val" =~ ^[0-9]+$ ]] || continue
            local secs=$(( val / 1000000 ))
            if [[ "$dur" != "0" ]]; then
                local pct
                pct=$(awk -v s="$secs" -v d="$dur" 'BEGIN{p=s/d*100; if(p>100)p=100; printf "%.0f", p}')
                printf "\r  progress: %3s%%   " "$pct"
            else
                printf "\r  progress: %ss processed   " "$secs"
            fi
        elif [[ "$key" == "progress" && "$val" == "end" ]]; then
            printf "\r  progress: 100%%   \n"
        fi
    done
    echo "  done -> $out"
}

# Pick a default output path for a given input + mode.
default_output() {
    local in="$1"
    local ext="mp4"; [[ "$MODE" == "prores" ]] && ext="mov"
    echo "${in%.*}_10bit.${ext}"
}

is_video() {
    case "${1,,}" in
        *.mp4|*.mov|*.mkv|*.avi|*.m4v|*.webm|*.mpg|*.mpeg|*.ts) return 0 ;;
        *) return 1 ;;
    esac
}

echo "Mode: $MODE   |   Deband strength: $STRENGTH (thr=$THR)"
echo

# ---------------------------------------------------------------------------
# Batch (folder) vs single file
# ---------------------------------------------------------------------------
if [[ -d "$INPUT" ]]; then
    shopt -s nullglob
    files=()
    for f in "$INPUT"/*; do
        [[ -f "$f" ]] && is_video "$f" && [[ "$f" != *_10bit.* ]] && files+=("$f")
    done
    shopt -u nullglob

    [[ ${#files[@]} -eq 0 ]] && { echo "No video files found in: $INPUT" >&2; exit 1; }

    echo "Batch mode: ${#files[@]} file(s) in $INPUT"
    echo
    n=0
    for f in "${files[@]}"; do
        n=$((n+1))
        echo "[$n/${#files[@]}] $(basename "$f")"
        convert_one "$f" "$(default_output "$f")"
        echo
    done
    echo "Batch complete: ${#files[@]} file(s)."
else
    [[ -f "$INPUT" ]] || { echo "Error: input '$INPUT' not found." >&2; exit 1; }
    [[ -z "$OUTPUT" ]] && OUTPUT="$(default_output "$INPUT")"
    convert_one "$INPUT" "$OUTPUT"
    echo
    echo "Verify bit depth with:"
    echo "  ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 \"$OUTPUT\""
fi
