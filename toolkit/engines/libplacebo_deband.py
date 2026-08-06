"""Optional libplacebo capability probe.

The shipped app deliberately keeps the proven CPU pipeline as its default. A
libplacebo build is only selectable after this probe confirms that the local
FFmpeg has the filter and the Vulkan/libplacebo build flags. No network or
model download is involved.
"""

import os
import json
import subprocess
import sys
from glob import glob

from .base import EngineCapabilities


class LibplaceboDebandEngine:
    engine_id = "libplacebo-deband-v1"
    display_name = "High-quality GPU deband (experimental)"
    capabilities = EngineCapabilities(
        processed_sample=True,
        gradient_analysis=True,
        local_only=True,
        requires_gpu_backend=True,
    )
    strength_thresholds = {"Low": "2", "Medium": "4", "High": "8", "Custom": None}

    def threshold_for(self, strength, custom):
        return self.strength_thresholds.get(strength) or str(custom)

    def runtime_environment(self, ffmpeg_path, base_env=None):
        """Return a Vulkan environment for the extracted GPU engine.

        The app extracts optional engine bundles to its writable app-data
        directory. That lets both macOS and Windows use a small runtime ICD
        manifest with an absolute driver path instead of relying on a process
        working directory or a system-wide environment setting.
        """
        root = os.path.dirname(os.path.abspath(ffmpeg_path))
        env = dict(base_env or os.environ)
        env.pop("DYLD_LIBRARY_PATH", None)
        icd_dir = os.path.join(root, "vulkan", "icd.d")

        if sys.platform.startswith("win"):
            # A bundled FFmpeg/libplacebo build may carry DLLs next to the
            # executable or in lib/. Keep this scoped to child FFmpeg probes
            # and exports; do not mutate the user's global PATH.
            runtime_dirs = [root, os.path.join(root, "lib")]
            existing = [directory for directory in runtime_dirs if os.path.isdir(directory)]
            if existing:
                env["PATH"] = os.pathsep.join(existing + [env.get("PATH", "")])

            # Most Windows Vulkan drivers are registered by the GPU driver and
            # need no manifest override. When a self-contained bundle supplies
            # one, rewrite its relative library path after extraction.
            manifests = sorted(glob(os.path.join(icd_dir, "*.json")))
            for source in manifests:
                try:
                    with open(source, encoding="utf-8") as handle:
                        manifest = json.load(handle)
                    raw_path = manifest.get("ICD", {}).get("library_path", "")
                    candidates = [
                        raw_path if os.path.isabs(raw_path) else os.path.join(os.path.dirname(source), raw_path),
                        os.path.join(root, raw_path),
                        os.path.join(root, "lib", os.path.basename(raw_path)),
                    ]
                    library = next((path for path in candidates if path and os.path.isfile(path)), "")
                    if not library:
                        continue
                    manifest.setdefault("ICD", {})["library_path"] = library
                    runtime = os.path.join(icd_dir, os.path.splitext(os.path.basename(source))[0] + "_runtime.json")
                    encoded = json.dumps(manifest, indent=2) + "\n"
                    previous = ""
                    try:
                        with open(runtime, encoding="utf-8") as handle:
                            previous = handle.read()
                    except OSError:
                        pass
                    if previous != encoded:
                        with open(runtime, "w", encoding="utf-8") as handle:
                            handle.write(encoded)
                    env["VK_ICD_FILENAMES"] = runtime
                    return env
                except (OSError, ValueError, TypeError):
                    continue
            return env

        source = os.path.join(icd_dir, "MoltenVK_icd.json")
        runtime = os.path.join(icd_dir, "MoltenVK_runtime_icd.json")
        molten = os.path.join(root, "lib", "libMoltenVK.dylib")
        if os.path.isfile(source) and os.path.isfile(molten):
            try:
                with open(source, encoding="utf-8") as handle:
                    manifest = json.load(handle)
                manifest.setdefault("ICD", {})["library_path"] = molten
                encoded = json.dumps(manifest, indent=2) + "\n"
                previous = None
                try:
                    with open(runtime, encoding="utf-8") as handle:
                        previous = handle.read()
                except OSError:
                    pass
                if previous != encoded:
                    with open(runtime, "w", encoding="utf-8") as handle:
                        handle.write(encoded)
                env["VK_ICD_FILENAMES"] = runtime
                return env
            except (OSError, ValueError, TypeError):
                pass
        if os.path.isfile(source):
            env["VK_ICD_FILENAMES"] = source
        return env

    def availability(self, ffmpeg_path="ffmpeg"):
        env = self.runtime_environment(ffmpeg_path)
        # Do not call `ffmpeg -filters` or `-buildconf` here. GPU runtimes can
        # stall while enumerating filters, which made working backends appear
        # unavailable. A one-frame conversion is both faster in practice and
        # the only meaningful proof that the filter, libraries, ICD and GPU
        # can work together.
        command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                   "-i", "color=s=16x16:d=1", "-vf", "libplacebo=deband=true",
                   "-frames:v", "1", "-f", "null", "-"]
        # The first use of an extracted GPU bundle can spend time validating
        # driver libraries. Retry once to distinguish a cold start from an
        # unusable Vulkan device.
        for attempt in range(2):
            try:
                probe = subprocess.run(command, capture_output=True, text=True, timeout=45, env=env)
            except subprocess.TimeoutExpired:
                if attempt == 0:
                    continue
                return {"available": False,
                        "reason": "GPU initialization did not finish. Restart the app, then try Faithful 10-bit if it persists.",
                        "engine": self.engine_id}
            except OSError as exc:
                return {"available": False, "reason": f"GPU runtime could not start: {exc}", "engine": self.engine_id}
            if probe.returncode == 0:
                return {"available": True, "reason": "libplacebo and Vulkan are usable", "engine": self.engine_id}
        return {"available": False, "reason": "Vulkan device initialization failed: " +
                (probe.stderr.strip()[-240:] or "unknown runtime error"), "engine": self.engine_id}

    def build_filter_chain(self, threshold, pix_fmt, **options):
        # This is intentionally guarded. The UI must not route jobs here until
        # availability() passes, because a stock FFmpeg will reject the filter.
        iterations = options.get("iterations", 2)
        radius = options.get("radius", 16)
        grain = options.get("grain", 5)
        return (f"libplacebo=deband=true:deband_iterations={iterations}:"
                f"deband_radius={radius}:deband_threshold={threshold}:deband_grain={grain},"
                f"format={pix_fmt}")


__all__ = ["LibplaceboDebandEngine"]
