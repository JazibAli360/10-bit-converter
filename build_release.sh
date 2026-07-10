#!/usr/bin/env bash
#
# build_release.sh — package the toolkit (code + bundled ffmpeg) into a clean,
# shareable zip with a first-run note. Run from the repo root:
#     ./build_release.sh
# Produces dist/10bit_converter_<version>.zip
#
set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"
NAME="10bit_converter_${VERSION}"
STAGE="dist/${NAME}"

rm -rf dist
mkdir -p "$STAGE"

# Copy the shipping toolkit (code + bundled bin), minus runtime/junk files.
rsync -a \
  --exclude '__pycache__' \
  --exclude '.DS_Store' \
  --exclude '.10bit_converter_settings.json' \
  toolkit/ "$STAGE/"

# Bundled ffmpeg check (arm64 / Apple Silicon).
if [[ -x "$STAGE/bin/arm64/ffmpeg" && -x "$STAGE/bin/arm64/ffprobe" ]]; then
  echo "✓ bundled ffmpeg included (arm64)"
else
  echo "! WARNING: bundled ffmpeg missing at toolkit/bin/arm64 — users would need a system ffmpeg."
fi

# Executable bits.
chmod +x "$STAGE"/*.command "$STAGE"/*.sh 2>/dev/null || true
[[ -d "$STAGE/bin/arm64" ]] && chmod +x "$STAGE/bin/arm64/"* 2>/dev/null || true

# First-run note at the top level.
cat > "$STAGE/START HERE.txt" <<'TXT'
8-bit -> 10-bit Converter
=========================

1. Double-click  Start_Here.command
2. FIRST TIME ONLY: if nothing happens, right-click Start_Here.command -> Open -> Open.
   (macOS blocks unsigned scripts once; after that, double-click works normally.)
3. Your browser opens the app at http://127.0.0.1:8766
   Leave the Terminal window open while you use it; close it to quit.

Requirements: Apple Silicon Mac (bundled ffmpeg is arm64) + Python 3
(preinstalled on macOS; the launcher offers to install it if missing).

What it does: debands 8-bit AI video, adds dither, and re-encodes to true 10-bit.
No AI — it never reinterprets your detail or texture. See README.md for details.

Built by Jazib Ali 360.
TXT

# Zip it (unix perms preserved).
( cd dist && zip -qr "${NAME}.zip" "${NAME}" )
echo "Built dist/${NAME}.zip"
du -h "dist/${NAME}.zip" | awk '{print "size:", $1}'
