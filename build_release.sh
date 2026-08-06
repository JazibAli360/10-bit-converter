#!/usr/bin/env bash
# Build a self-contained Apple Silicon macOS app archive.
# The output does not require Python, Homebrew, or FFmpeg on the recipient Mac.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
TOOLKIT="$ROOT/toolkit"
PYTHON="${PYTHON:-$ROOT/.venv-native/bin/python}"
VERSION="${VERSION:-0.1.0}"
APP="$TOOLKIT/dist/10-bit Converter.app"
OUT="$ROOT/dist/10-bit-Converter-${VERSION}-macos-arm64.zip"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing native build environment: $PYTHON"
  echo "Create it with: python3 -m venv .venv-native && .venv-native/bin/python -m pip install -r toolkit/requirements-native.txt"
  exit 1
fi

pushd "$TOOLKIT" >/dev/null
"$PYTHON" setup_native.py py2app --arch arm64
codesign --verify --deep --strict "$APP"

for required in \
  "$APP/Contents/Frameworks/Python3.framework/Versions/3.9/Python3" \
  "$APP/Contents/Resources/bin/arm64/ffmpeg" \
  "$APP/Contents/Resources/bin/arm64/ffprobe" \
  "$APP/Contents/Resources/index.html" \
  "$APP/Contents/Resources/JZB.png" \
  "$APP/Contents/Resources/bin/arm64/libplacebo.bundle.zip"; do
  [[ -e "$required" ]] || { echo "Packaging failed: missing $required"; exit 1; }
done

# Presence is not enough: a tiny dynamically linked FFmpeg can look bundled
# while its required dylibs are absent. Exercise both standard tools from the
# final app bundle before producing a release.
"$APP/Contents/Resources/bin/arm64/ffmpeg" -hide_banner -version >/dev/null
"$APP/Contents/Resources/bin/arm64/ffprobe" -hide_banner -version >/dev/null
if [[ "${REQUIRE_LIBPLACEBO:-0}" == "1" ]]; then
  "$PYTHON" "$TOOLKIT/qa/release_smoke_test.py" --require-gpu "$APP"
else
  "$PYTHON" "$TOOLKIT/qa/release_smoke_test.py" "$APP"
fi


if [[ "${REQUIRE_LIBPLACEBO:-0}" == "1" ]]; then
  BUNDLE="$APP/Contents/Resources/bin/arm64/libplacebo.bundle.zip"
  unzip -l "$BUNDLE" | grep -q 'libplacebo/ffmpeg' || {
    echo "Release gate failed: optional bundle has no libplacebo FFmpeg"; exit 1;
  }
  unzip -l "$BUNDLE" | grep -q 'libplacebo/lib/libMoltenVK.dylib' || {
    echo "Release gate failed: optional bundle has no MoltenVK runtime"; exit 1;
  }
fi
popd >/dev/null

mkdir -p "$ROOT/dist"
rm -f "$OUT"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT"
echo "Built standalone app archive: $OUT"
