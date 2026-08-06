#!/usr/bin/env bash
#
# Start_Here.command  —  THE ONLY FILE MOST PEOPLE NEED TO RUN.
#
# Double-click this file. It will:
#   1. Check for what the app needs (bundled ffmpeg + Python 3 — no GUI toolkit).
#   2. Offer to install anything that's missing — you just confirm.
#   3. Start the native converter window when available (browser fallback).
#
# FIRST RUN: macOS blocks unsigned scripts by default. If double-click does
# nothing, right-click this file -> Open -> Open. You only do this once.

set -uo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
say()  { printf "%s\n" "$*"; }
ok()   { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}!${RESET} %s\n" "$*"; }
err()  { printf "${RED}✗${RESET} %s\n" "$*"; }

ask() {
  # ask "question" -> returns 0 for yes, 1 for no (default yes)
  local reply
  printf "%s ${DIM}[Y/n]${RESET} " "$1"
  read -r reply </dev/tty || reply="y"
  [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

clear 2>/dev/null || true
say "${BOLD}8-bit → 10-bit Converter — setup & launch${RESET}"
say "${DIM}This checks what you have and installs anything missing.${RESET}"
say

# --- 1. macOS check (informational) ---
if [[ "$(uname)" != "Darwin" ]]; then
  warn "This launcher targets macOS. On Linux, install ffmpeg + python3-tk"
  warn "with your package manager, then run:  python3 10bit_converter_gui.py"
fi

# --- 2. ffmpeg / ffprobe: prefer the BUNDLED build (zero install) ---
BUNDLED_BIN="$(pwd)/bin/$(uname -m)"
if [[ -x "$BUNDLED_BIN/ffmpeg" && -x "$BUNDLED_BIN/ffprobe" ]]; then
  # Clear Gatekeeper quarantine so the bundled binaries run after a download.
  xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true
  export PATH="$BUNDLED_BIN:$PATH"
  ok "Using bundled ffmpeg ($(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')) — no install needed."
elif command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ok "ffmpeg found on your system ($(ffmpeg -version | head -1 | awk '{print $3}'))."
else
  warn "No bundled ffmpeg for this Mac's architecture ($(uname -m)) and none installed."
  if ! command -v brew >/dev/null 2>&1; then
    if ask "Install Homebrew (needed to install ffmpeg)?"; then
      say "Installing Homebrew — follow any prompts it shows…"
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
      [[ -x /usr/local/bin/brew ]] && eval "$(/usr/local/bin/brew shellenv)"
    else
      err "Can't continue without ffmpeg. Exiting."
      read -r -p "Press Return to close." </dev/tty; exit 1
    fi
  fi
  if ask "Install ffmpeg now?"; then
    brew install ffmpeg
  else
    err "Can't convert without ffmpeg. Exiting."
    read -r -p "Press Return to close." </dev/tty; exit 1
  fi
fi

# --- 4. Python 3 (runs the local web app — stdlib only, no GUI toolkit needed) ---
PYBIN="$(command -v python3 || true)"
if [[ -z "$PYBIN" ]]; then
  warn "Python 3 is not installed (needed to run the app)."
  if command -v brew >/dev/null 2>&1; then
    if ask "Install Python 3 now?"; then
      brew install python
      PYBIN="$(command -v python3 || true)"
    fi
  else
    warn "Install the Command Line Tools (run 'xcode-select --install') or Python from python.org, then re-run."
  fi
fi

# --- 5. Launch the app (native PyWebView window when installed; browser fallback) ---
say
if [[ -n "$PYBIN" ]]; then
  ok "All set. Opening the converter…"
  say "${DIM}A native window opens when PyWebView is installed; otherwise the browser fallback opens.${RESET}"
  exec "$PYBIN" "./server.py"
else
  err "Python 3 isn't available, so the app can't start."
  say "You can still convert from Terminal with:"
  say "  ${BOLD}./8bit_to_10bit.sh yourvideo.mp4${RESET}"
  read -r -p "Press Return to close." </dev/tty
fi
