"""Cancellable, isolated preview and scope rendering for the local app.

This module deliberately owns no HTTP state.  The server supplies the selected
file authorizer plus the active Faithful filter builders, and exposes the
generated files through its own capability-checked routes.
"""

import math
import os
import struct
import subprocess
import tempfile
import uuid
import zlib

from media_probe import probe_duration, probe_info


class PreviewService:
    """Render scoped, user-triggered preview artifacts in temporary folders."""

    def __init__(self, intake_dir, authorize_path, engine, deband_chain_builder, filter_builder):
        self.intake_dir = intake_dir
        self.authorize_path = authorize_path
        self.engine = engine
        self.deband_chain_builder = deband_chain_builder
        self.filter_builder = filter_builder
        self._artifacts = {"scope": {}, "compare": {}, "filmstrip": {}}

    def image_path(self, kind, token, which):
        directory = self._artifacts.get(kind, {}).get(token)
        if not directory:
            return None
        candidate = os.path.join(directory, os.path.basename(which) + ".png")
        return candidate if os.path.isfile(candidate) else None

    def cleanup(self):
        directories = []
        for artifacts in self._artifacts.values():
            directories.extend(artifacts.values())
            artifacts.clear()
        for directory in directories:
            try:
                import shutil
                shutil.rmtree(directory, ignore_errors=True)
            except OSError:
                pass

    @staticmethod
    def _png_chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    def _write_rgb_png(self, path, width, height, pixels):
        rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
        payload = (b"\x89PNG\r\n\x1a\n" + self._png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                   + self._png_chunk(b"IDAT", zlib.compress(rows, 6)) + self._png_chunk(b"IEND", b""))
        with open(path, "wb") as handle:
            handle.write(payload)

    def _render_histogram(self, rgb, sample_width, sample_height, out_path):
        """Make a detailed RGB histogram without a third-party image library."""
        width, height = 1600, 900
        pixels = bytearray(width * height * 3)
        for index in range(0, len(pixels), 3):
            pixels[index:index + 3] = b"\x05\x07\x0a"

        def pixel(x, y, colour):
            if 0 <= x < width and 0 <= y < height:
                offset = (y * width + x) * 3
                pixels[offset:offset + 3] = bytes(colour)

        left, right, top, bottom = 52, 18, 20, 30
        plot_width, plot_height = width - left - right, height - top - bottom
        panel_height = plot_height // 3
        grid, colours = (78, 62, 10), ((255, 72, 72), (70, 230, 112), (80, 145, 255))
        bins = [[0] * 256 for _ in range(3)]
        for index in range(0, min(len(rgb), sample_width * sample_height * 3), 3):
            bins[0][rgb[index]] += 1
            bins[1][rgb[index + 1]] += 1
            bins[2][rgb[index + 2]] += 1
        for channel in range(3):
            y0 = top + channel * panel_height
            y1 = top + (channel + 1) * panel_height - 8
            for level in range(5):
                y = y0 + round((y1 - y0) * level / 4)
                for x in range(left, width - right):
                    pixel(x, y, grid)
            for level in range(9):
                x = left + round(plot_width * level / 8)
                for y in range(y0, y1 + 1):
                    pixel(x, y, grid)
            peak_log = math.log1p(max(bins[channel]) or 1)
            dim = tuple(max(1, colour // 4) for colour in colours[channel])
            for level, count in enumerate(bins[channel]):
                x = left + round(level * plot_width / 255)
                y = y1 - round(math.log1p(count) / peak_log * (y1 - y0 - 8))
                for fill_y in range(y, y1 + 1):
                    pixel(x, fill_y, dim)
                for trace_y in range(max(y0, y - 1), min(y1 + 1, y + 2)):
                    pixel(x, trace_y, colours[channel])
        self._write_rgb_png(out_path, width, height, pixels)

    @staticmethod
    def _scope_rgb_frame(in_path, timestamp, video_filter, source_info):
        source_width = source_info.get("width") or 1920
        source_height = source_info.get("height") or 1080
        width = max(2, min(960, source_width) // 2 * 2)
        height = max(2, round(source_height * width / source_width) // 2 * 2)
        filters = ",".join(part for part in (video_filter, f"scale={width}:{height}:flags=lanczos", "format=rgb24") if part)
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{timestamp}", "-i", in_path, "-frames:v", "1",
             "-vf", filters, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        expected = width * height * 3
        if result.returncode != 0 or len(result.stdout) != expected:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or "could not read frame pixels")
        return result.stdout, width, height

    def render_scopes(self, in_path, strength, settings, timestamp=None):
        threshold = self.engine.threshold_for(strength, settings.get("thr_custom", "0.03"))
        chain = self.deband_chain_builder(threshold, settings["deband_range"], settings["deband_blur"],
                                          settings["dither"], settings.get("deflicker", False),
                                          settings.get("max_quality", False), settings.get("denoise", "off"))
        outdir = tempfile.mkdtemp(prefix="scopes_")
        duration = probe_duration(in_path)
        if timestamp is None:
            timestamp = max(0.0, duration * 0.4) if duration else 1.0
        timestamp = max(0.0, min(float(timestamp), max(0.0, duration - 0.05))) if duration else float(timestamp)
        sample = "scale=w='min(1280,iw)':h=-2:flags=lanczos"
        jobs = {}
        for prefix, filters in (("src", sample), ("aft", f"{chain},{sample}")):
            jobs[f"{prefix}_waveform"] = (f"{filters},format=yuv444p,waveform=mode=column:display=overlay:components=1:"
                                             "filter=lowpass:scale=ire:graticule=orange:opacity=0.8:intensity=0.15:mirror=0,scale=1600:900:flags=lanczos")
            jobs[f"{prefix}_parade"] = (f"{filters},format=gbrp,waveform=mode=column:display=parade:components=7:"
                                           "filter=color:scale=ire:graticule=orange:opacity=0.8:intensity=0.15:mirror=0,scale=1600:900:flags=lanczos")
            jobs[f"{prefix}_vectorscope"] = (f"{filters},format=yuv444p,vectorscope=mode=color3:colorspace=709:intensity=0.18:"
                                                "graticule=color:opacity=0.8,scale=1200:1200:flags=lanczos")
        errors = {}
        for name, video_filter in jobs.items():
            output = os.path.join(outdir, name + ".png")
            result = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp}", "-i", in_path,
                                     "-frames:v", "1", "-vf", video_filter, output], capture_output=True, text=True)
            if result.returncode != 0 or not os.path.isfile(output):
                errors[name] = result.stderr.strip() or f"ffmpeg exited {result.returncode}"
        source_info = probe_info(in_path)
        for prefix, video_filter in (("src", sample), ("aft", f"{chain},{sample}")):
            try:
                rgb, width, height = self._scope_rgb_frame(in_path, timestamp, video_filter, source_info)
                self._render_histogram(rgb, width, height, os.path.join(outdir, f"{prefix}_histogram.png"))
            except Exception as exc:
                errors[f"{prefix}_histogram"] = str(exc)
        token = os.path.basename(outdir)
        self._artifacts["scope"][token] = outdir
        return token, errors, timestamp, duration

    def render_processed_sample(self, in_path, strength, settings, timestamp=None):
        duration = probe_duration(in_path)
        length = min(3.0, duration) if duration else 3.0
        if timestamp is None:
            timestamp = duration * 0.4 if duration else 0.0
        timestamp = max(0.0, min(float(timestamp), max(0.0, duration - length))) if duration else max(0.0, float(timestamp))
        threshold = self.engine.threshold_for(strength, settings.get("thr_custom", "0.03"))
        filters = self.filter_builder(threshold, "yuv420p10le", settings["deband_range"], settings["deband_blur"],
                                      settings["dither"], settings.get("deflicker", False),
                                      settings.get("max_quality", False), settings.get("denoise", "off"))
        output = os.path.join(self.intake_dir, f"processed_sample_{uuid.uuid4().hex}.mp4")
        command = ["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", in_path, "-t", f"{length:.3f}",
                   "-map", "0:v:0", "-an", "-vf", filters, "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                   "-preset", "veryfast", "-crf", "18", "-tag:v", "hvc1", "-movflags", "+faststart", output]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if result.returncode or not os.path.isfile(output):
            raise RuntimeError(result.stderr.strip() or "FFmpeg could not render the sample")
        return self.authorize_path(output), round(timestamp, 2), round(length, 2)

    def render_filmstrip(self, in_path, count=8):
        outdir = tempfile.mkdtemp(prefix="strip_")
        duration = probe_duration(in_path)
        times = [round(duration * (index + 0.5) / count, 2) for index in range(count)] if duration else [1.0]
        for index, timestamp in enumerate(times):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp}", "-i", in_path,
                            "-frames:v", "1", "-vf", "scale=160:-2", os.path.join(outdir, f"f{index}.png")], capture_output=True)
        token = os.path.basename(outdir)
        self._artifacts["filmstrip"][token] = outdir
        return token, times

    def render_compare(self, source, output, timestamp, zoom=None):
        outdir = tempfile.mkdtemp(prefix="cmp_")
        crop = ""
        if zoom and zoom.get("factor", 1) > 1:
            factor = max(1.0, float(zoom["factor"]))
            center_x, center_y = float(zoom.get("cx", 0.5)), float(zoom.get("cy", 0.5))
            width_fraction = height_fraction = 1.0 / factor
            x_fraction = max(0.0, min(1 - width_fraction, center_x - width_fraction / 2))
            y_fraction = max(0.0, min(1 - height_fraction, center_y - height_fraction / 2))
            crop = f"crop=iw*{width_fraction}:ih*{height_fraction}:iw*{x_fraction}:ih*{y_fraction},"
        for label, path in (("before", source), ("after", output)):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp}", "-i", path, "-frames:v", "1",
                            "-vf", f"{crop}scale=1000:-2", os.path.join(outdir, label + ".png")], capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp}", "-i", source, "-ss", f"{timestamp}",
                        "-i", output, "-frames:v", "1", "-filter_complex",
                        f"[0:v]{crop}scale=1000:-2[a];[1:v]{crop}scale=1000:-2[b];[a][b]blend=all_mode=difference,lutrgb=r=val*8:g=val*8:b=val*8",
                        os.path.join(outdir, "diff.png")], capture_output=True)
        token = os.path.basename(outdir)
        self._artifacts["compare"][token] = outdir
        return token
