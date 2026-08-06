"""Export-plan construction, kept independent from HTTP and queue rendering."""

import os
import subprocess

from media_probe import (pixfmt_bits, probe_audio_codec, probe_bitrate_kbps,
                         probe_colour_metadata, probe_duration, probe_pix_fmt, probe_subtitle_codecs)


MP4_COPY_AUDIO = {"aac", "mp3", "ac3", "eac3"}
VALID_MODES = {"HEVC (smaller, delivery)", "H.264 (10-bit, delivery)",
               "ProRes 4444 (grading, huge file)"}
VALID_RATES = {"Match source", "Quality (CRF)", "Custom"}
EXPORT_PROVENANCE = "Processed with 10-bit Converter by Jazib Ali 360"


class ConversionPlanner:
    """Resolve one queue item into a complete, engine-neutral export plan."""

    def __init__(self, intake_dir, strength_thresholds, default_engine):
        self.intake_dir = intake_dir
        self.strength_thresholds = strength_thresholds
        self.default_engine = default_engine

    @staticmethod
    def mode_kind(mode):
        value = str(mode or "")
        if value.startswith("ProRes"):
            return "prores"
        if value.startswith("H.264"):
            return "h264"
        return "hevc"

    def normalise(self, item, default_mode, default_strength, default_rate):
        override = item.get("override") if isinstance(item.get("override"), dict) else {}
        mode = override.get("mode", default_mode)
        strength = override.get("strength", default_strength)
        rate = override.get("rate", default_rate)
        return (mode if mode in VALID_MODES else default_mode,
                strength if strength in self.strength_thresholds else default_strength,
                rate if rate in VALID_RATES else default_rate, override)

    def output_path(self, input_path, is_prores, destination, suffix):
        base = os.path.splitext(os.path.basename(input_path))[0]
        extension = "mov" if is_prores else "mp4"
        if destination:
            directory = destination
        elif input_path.startswith(self.intake_dir):
            directory = os.path.expanduser("~/Downloads")
        else:
            directory = os.path.dirname(input_path)
        return os.path.join(directory, f"{base}{suffix}.{extension}")

    @staticmethod
    def format_label(mode):
        return {"prores": "ProRes 4444", "h264": "H.264 10-bit", "hevc": "HEVC"}[ConversionPlanner.mode_kind(mode)]

    @staticmethod
    def stream_map_args():
        return ["-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0"]

    @staticmethod
    def provenance_args():
        """Add a human-readable, portable note without removing source metadata."""
        return ["-metadata", f"comment={EXPORT_PROVENANCE}"]

    @staticmethod
    def subtitle_args(input_path):
        codecs = probe_subtitle_codecs(input_path)
        if not codecs:
            return [], ""
        if all(codec in {"mov_text", "subrip", "webvtt", "ass", "ssa"} for codec in codecs):
            return ["-map", "0:s?", "-c:s", "mov_text"], ""
        return [], f"{len(codecs)} unsupported subtitle track(s) were not copied to this container"

    @staticmethod
    def audio_args(input_path, is_prores, audio_mode):
        codec = probe_audio_codec(input_path)
        if not codec:
            return ["-an"]
        if audio_mode == "aac" and not is_prores:
            return ["-c:a", "aac", "-b:a", "192k"]
        if is_prores or codec in MP4_COPY_AUDIO:
            return ["-c:a", "copy"]
        return ["-c:a", "aac", "-b:a", "192k"]

    @staticmethod
    def colour_args(input_path, source_interpretation="preserve"):
        values = probe_colour_metadata(input_path)
        assumptions = {
            "rec709_limited": {"color_primaries": "bt709", "color_transfer": "bt709",
                                "color_space": "bt709", "color_range": "tv"},
            "srgb_full": {"color_primaries": "bt709", "color_transfer": "iec61966-2-1",
                          "color_space": "bt709", "color_range": "pc"},
        }
        if source_interpretation in assumptions:
            values = assumptions[source_interpretation]
        invalid, args = {"", "unknown", "N/A", "reserved"}, []
        for key, flag in (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"),
                          ("color_space", "-colorspace")):
            if values.get(key) not in invalid:
                args.extend((flag, values[key]))
        if values.get("color_range") in {"tv", "pc", "limited", "full"}:
            args.extend(("-color_range", values["color_range"]))
        return args

    @staticmethod
    def colour_encoder_args(kind, colour_args):
        """Write supported SDR colour tags into the encoded video bitstream."""
        values = dict(zip(colour_args[::2], colour_args[1::2]))
        primary = values.get("-color_primaries")
        transfer = values.get("-color_trc")
        matrix = values.get("-colorspace")
        if not (primary and transfer and matrix):
            return []
        range_name = {"tv": "limited", "pc": "full", "limited": "limited", "full": "full"}.get(
            values.get("-color_range"), "limited")
        params = f"colorprim={primary}:transfer={transfer}:colormatrix={matrix}:range={range_name}"
        return (["-x265-params", params] if kind == "hevc" else ["-x264-params", params])

    @staticmethod
    def rate_args(rate, settings, input_path):
        if rate == "Match source":
            bitrate = probe_bitrate_kbps(input_path)
            if bitrate > 0:
                return ["-b:v", f"{bitrate}k", "-maxrate", f"{int(bitrate * 1.45)}k",
                        "-bufsize", f"{bitrate * 2}k"]
        elif rate == "Custom":
            bitrate = int(float(settings.get("target_mbps", 12.0)) * 1000)
            return ["-b:v", f"{bitrate}k", "-maxrate", f"{int(bitrate * 1.45)}k",
                    "-bufsize", f"{bitrate * 2}k"]
        return ["-crf", str(settings["crf"])]

    def plan(self, item, default_mode, default_strength, default_rate, settings, engine):
        mode, strength, rate, override = self.normalise(item, default_mode, default_strength, default_rate)
        item_settings = dict(settings)
        if "target_mbps" in override:
            try:
                item_settings["target_mbps"] = max(1.0, float(override["target_mbps"]))
            except (TypeError, ValueError):
                pass
        kind = self.mode_kind(mode)
        is_prores = kind == "prores"
        threshold = engine.threshold_for(strength, str(item_settings.get("thr_custom", "0.03")))
        pixel_format = "yuv444p10le" if is_prores else "yuv420p10le"
        if engine.engine_id == self.default_engine.engine_id:
            filters = engine.build_filter_chain(threshold, pixel_format, range=item_settings["deband_range"],
                                                blur=item_settings["deband_blur"], dither=item_settings["dither"],
                                                deflicker=item_settings.get("deflicker", False),
                                                max_quality=item_settings.get("max_quality", False),
                                                denoise=item_settings.get("denoise", "off"),
                                                colour_safe=item_settings.get("colour_safe", False))
        else:
            filters = engine.build_filter_chain(threshold, pixel_format, iterations=2, radius=16, grain=5)
        input_path = item["path"]
        streams = [*self.stream_map_args(), *self.subtitle_args(input_path)[0]]
        audio = self.audio_args(input_path, is_prores, item_settings.get("audio", "copy"))
        colour = self.colour_args(input_path, item_settings.get("source_interpretation", "preserve"))
        colour_encoder = self.colour_encoder_args(kind, colour) if not is_prores else []
        rate_arguments = [] if is_prores else self.rate_args(rate, item_settings, input_path)
        source_bits = pixfmt_bits(probe_pix_fmt(input_path))
        profile = f"{self.format_label(mode)} · {strength} deband"
        if item_settings.get("colour_safe"):
            profile += " · AI Colour-Safe"
        profile += " · codec-managed" if is_prores else f" · {rate}"
        return {"mode": mode, "strength": strength, "rate": rate, "override": override, "settings": item_settings,
                "kind": kind, "is_prores": is_prores, "filters": filters, "streams": streams, "audio": audio,
                "colour": colour, "rate_args": rate_arguments, "source_bits": source_bits,
                "colour_encoder": colour_encoder, "provenance": self.provenance_args(),
                "duration": probe_duration(input_path), "profile": profile,
                "subtitle_note": self.subtitle_args(input_path)[1]}
