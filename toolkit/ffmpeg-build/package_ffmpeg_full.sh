#!/usr/bin/env bash
set -euo pipefail

# Package Homebrew's arm64 ffmpeg-full without leaving Homebrew paths in the
# app. This is a build-time script; the release contains only the copied
# executables, dylibs, and MoltenVK ICD manifest.
SRC="${1:-/opt/homebrew/opt/ffmpeg-full}"
# Keep the optional GPU runtime completely separate from the proven standard
# converter. Writing it into bin/arm64 would replace the app's normal FFmpeg
# with an executable whose dependency closure lives only in the optional zip.
OUT="${2:-$(cd "$(dirname "$0")/.." && pwd)/bin/arm64/libplacebo}"
MOLTEN="${MOLTEN_VK_PREFIX:-/opt/homebrew/opt/molten-vk}"
mkdir -p "$OUT/lib" "$OUT/vulkan/icd.d"
chmod -R u+w "$OUT" 2>/dev/null || true
cp "$SRC/bin/ffmpeg" "$OUT/ffmpeg"
cp "$SRC/bin/ffprobe" "$OUT/ffprobe"

queue=("$OUT/ffmpeg" "$OUT/ffprobe")
seen_file="$OUT/.dependency-seen"
: > "$seen_file"
while ((${#queue[@]})); do
  obj="${queue[0]}"; queue=("${queue[@]:1}")
  [[ -f "$obj" ]] || continue
  grep -Fxq "$obj" "$seen_file" && continue
  echo "$obj" >> "$seen_file"
  while IFS= read -r dep; do
    dep="${dep##$'\t'}"
    dep="${dep%% (*}"
    if [[ "$dep" == @rpath/* ]]; then
      name="${dep#@rpath/}"
      dep="/opt/homebrew/lib/$name"
      if [[ ! -f "$dep" ]]; then
        for candidate in /opt/homebrew/Cellar/*/*/lib/"$name"; do
          if [[ -f "$candidate" ]]; then dep="$candidate"; break; fi
        done
      fi
    fi
    [[ "$dep" == /opt/homebrew/* && -f "$dep" ]] || continue
    name="$(basename "$dep")"
    target="$OUT/lib/$name"
    [[ -f "$target" ]] || cp "$dep" "$target"
    queue+=("$target")
  done < <(otool -L "$obj" | tail -n +2)
done
rm -f "$seen_file"

cp "$MOLTEN/lib/libMoltenVK.dylib" "$OUT/lib/libMoltenVK.dylib"
cp "$MOLTEN/etc/vulkan/icd.d/MoltenVK_icd.json" "$OUT/vulkan/icd.d/MoltenVK_icd.json"

for obj in "$OUT/ffmpeg" "$OUT/ffprobe" "$OUT/lib"/*.dylib; do
  [[ -f "$obj" ]] || continue
  if [[ "$obj" == "$OUT/lib/"* ]]; then
    install_name_tool -add_rpath '@loader_path' "$obj" 2>/dev/null || true
    install_name_tool -id "@rpath/$(basename "$obj")" "$obj" 2>/dev/null || true
  else
    install_name_tool -add_rpath '@loader_path/lib' "$obj" 2>/dev/null || true
  fi
  while IFS= read -r dep; do
    dep="${dep##$'\t'}"; dep="${dep%% (*}"
    [[ "$dep" == /opt/homebrew/* ]] || continue
    install_name_tool -change "$dep" "@rpath/$(basename "$dep")" "$obj" 2>/dev/null || true
  done < <(otool -L "$obj" | tail -n +2)
done

# install_name_tool invalidates the source signature. Re-sign the optional
# executables after all path rewrites so macOS does not kill the process before
# the runtime capability probe can report a useful error.
codesign --force --sign - "$OUT/ffmpeg" "$OUT/ffprobe" >/dev/null 2>&1 || true
for dylib in "$OUT/lib"/*.dylib; do
  codesign --force --sign - "$dylib" >/dev/null 2>&1 || true
done

# Keep the manifest relative to the bundled bin/arm64 directory.
# The ICD lives in <engine>/vulkan/icd.d; two parents reaches <engine>.
# Three parents points outside the engine and makes Vulkan falsely report an
# incompatible driver after packaging.
sed -i '' 's#"library_path"[[:space:]]*:[[:space:]]*"[^"]*"#"library_path" : "../../lib/libMoltenVK.dylib"#' \
  "$OUT/vulkan/icd.d/MoltenVK_icd.json"
# Static packaging gate only. Runtime Vulkan/device validation belongs to the
# application probe because it is hardware- and driver-dependent.
otool -L "$OUT/lib/libavfilter.11.dylib" | grep -q libplacebo
rm -f "$(dirname "$OUT")/$(basename "$OUT").bundle.zip"
(cd "$(dirname "$OUT")" && ditto -c -k --sequesterRsrc --keepParent \
  "$(basename "$OUT")" "$(basename "$OUT").bundle.zip")
echo "Packaged libplacebo FFmpeg into $OUT"
