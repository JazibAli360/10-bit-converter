#!/usr/bin/env bash
#
# 8bit_to_10bit.command
# Mac double-click / drag-and-drop tool.
#
# HOW TO USE:
#   1. Double-click this file once to let macOS register it (you may need to
#      right-click -> Open the first time, since it's an unsigned script).
#   2. After that, DRAG one or more video files -- or a whole FOLDER of them
#      -- and DROP them onto this file's icon in Finder. Each is converted and
#      saved next to the original, then the folder is revealed for you.
#   3. On launch it asks you which output format and deband strength to use.
#
# Requires ffmpeg. If you don't have it, this script will tell you how to
# install it via Homebrew.

set -euo pipefail
cd "$(dirname "$0")"

# --- Prefer bundled ffmpeg/ffprobe (bin/<arch>), fall back to system ---
BUNDLED_BIN="$(pwd)/bin/$(uname -m)"
if [[ -x "$BUNDLED_BIN/ffmpeg" && -x "$BUNDLED_BIN/ffprobe" ]]; then
  xattr -dr com.apple.quarantine "$BUNDLED_BIN" 2>/dev/null || true
  export PATH="$BUNDLED_BIN:$PATH"
fi

# --- Check ffmpeg ---
if ! command -v ffmpeg >/dev/null 2>&1; then
  osascript -e 'display alert "ffmpeg not found" message "Install it by opening Terminal and running:\n\nbrew install ffmpeg\n\n(If you dont have Homebrew: install it first from brew.sh)"'
  exit 1
fi

# --- Ask for output format (dropdown) ---
MODE_CHOICE=$(osascript -e 'choose from list {"HEVC (smaller, delivery)", "ProRes 4444 (grading, huge file)"} with prompt "Output format:" default items {"HEVC (smaller, delivery)"} without multiple selections allowed' 2>/dev/null || echo "false")
[[ "$MODE_CHOICE" == "false" ]] && exit 0
if [[ "$MODE_CHOICE" == ProRes* ]]; then MODE="prores"; else MODE="hevc"; fi

# --- Ask for deband strength (dropdown) ---
STRENGTH_CHOICE=$(osascript -e 'choose from list {"Low (subtle, preserves detail)", "Medium (balanced)", "High (smooth skies/gradients)"} with prompt "Deband strength:" default items {"Medium (balanced)"} without multiple selections allowed' 2>/dev/null || echo "false")
[[ "$STRENGTH_CHOICE" == "false" ]] && exit 0
case "$STRENGTH_CHOICE" in
  Low*)  THR="0.01" ;;
  High*) THR="0.05" ;;
  *)     THR="0.02" ;;
esac

FILTERS_420="deband=1thr=${THR}:2thr=${THR}:3thr=${THR}:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv420p10le"
FILTERS_444="deband=1thr=${THR}:2thr=${THR}:3thr=${THR}:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv444p10le"

# --- Collect inputs (dropped files/folders, or a picker if launched empty) ---
INPUTS=()
if [[ $# -ge 1 ]]; then
  INPUTS=("$@")
else
  PICK=$(osascript -e 'POSIX path of (choose file with prompt "Select a video to convert to 10-bit")' 2>/dev/null || echo "")
  [[ -z "$PICK" ]] && exit 0
  INPUTS=("$PICK")
fi

is_video() {
  case "${1,,}" in
    *.mp4|*.mov|*.mkv|*.avi|*.m4v|*.webm|*.mpg|*.mpeg|*.ts) return 0 ;;
    *) return 1 ;;
  esac
}

# Expand any dropped folders into their contained video files.
FILES=()
for item in "${INPUTS[@]}"; do
  if [[ -d "$item" ]]; then
    shopt -s nullglob
    for f in "$item"/*; do
      [[ -f "$f" ]] && is_video "$f" && [[ "$f" != *_10bit.* ]] && FILES+=("$f")
    done
    shopt -u nullglob
  elif [[ -f "$item" ]]; then
    FILES+=("$item")
  fi
done

[[ ${#FILES[@]} -eq 0 ]] && { osascript -e 'display alert "Nothing to convert" message "No video files were found in what you dropped."'; exit 1; }

convert_one() {
  local in="$1"
  local dir base name out
  dir=$(dirname "$in"); base=$(basename "$in"); name="${base%.*}"
  if [[ "$MODE" == "prores" ]]; then out="$dir/${name}_10bit.mov"; else out="$dir/${name}_10bit.mp4"; fi

  local dur
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in" 2>/dev/null || echo "0")
  [[ -z "$dur" || "$dur" == "N/A" ]] && dur="0"

  echo "Converting: $in"
  echo "Output:     $out"

  if [[ "$MODE" == "prores" ]]; then
    ffmpeg -y -nostats -i "$in" -vf "$FILTERS_444" \
      -c:v prores_ks -profile:v 4444 -pix_fmt yuv444p10le -c:a pcm_s16le \
      -progress pipe:1 "$out" 2>/dev/null | _progress "$dur"
  else
    ffmpeg -y -nostats -i "$in" -vf "$FILTERS_420" \
      -c:v libx265 -pix_fmt yuv420p10le -crf 18 -preset slow -tag:v hvc1 \
      -c:a aac -b:a 192k \
      -progress pipe:1 "$out" 2>/dev/null | _progress "$dur"
  fi
  echo "  done."
  LAST_OUTPUT="$out"
}

# Reads ffmpeg -progress key=value stream and prints a live percentage.
_progress() {
  local dur="$1"
  while IFS='=' read -r key val; do
    if [[ "$key" == "out_time_us" || "$key" == "out_time_ms" ]]; then
      [[ "$val" =~ ^[0-9]+$ ]] || continue
      local secs=$(( val / 1000000 ))
      if [[ "$dur" != "0" ]]; then
        local pct
        pct=$(awk -v s="$secs" -v d="$dur" 'BEGIN{p=s/d*100; if(p>100)p=100; printf "%.0f", p}')
        printf "\r  progress: %3s%%   " "$pct"
      else
        printf "\r  progress: %ss   " "$secs"
      fi
    elif [[ "$key" == "progress" && "$val" == "end" ]]; then
      printf "\r  progress: 100%%   \n"
    fi
  done
}

LAST_OUTPUT=""
echo "Mode: $MODE   |   Deband strength threshold: $THR"
echo
n=0
for f in "${FILES[@]}"; do
  n=$((n+1))
  echo "[$n/${#FILES[@]}] $(basename "$f")"
  convert_one "$f"
  echo
done

# --- Notify + reveal in Finder ---
if [[ ${#FILES[@]} -eq 1 ]]; then
  osascript -e "display notification \"Saved to $(basename "$LAST_OUTPUT")\" with title \"10-bit conversion done\""
else
  osascript -e "display notification \"${#FILES[@]} files converted\" with title \"10-bit conversion done\""
fi
[[ -n "$LAST_OUTPUT" ]] && open -R "$LAST_OUTPUT"
