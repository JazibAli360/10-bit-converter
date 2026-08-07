"""Built-in FFmpeg deband + dither engine metadata and profile resolution."""

from .base import EngineCapabilities


class FFmpegDebandEngine:
    engine_id = "ffmpeg-deband-v1"
    display_name = "Faithful FFmpeg 10-bit"
    capabilities = EngineCapabilities(
        processed_sample=True,
        gradient_analysis=True,
        local_only=True,
    )
    strength_thresholds = {
        "Low": "0.01",
        "Medium": "0.02",
        "High": "0.05",
        "Custom": None,
    }

    def threshold_for(self, strength: str, custom: str) -> str:
        return self.strength_thresholds.get(strength) or str(custom)

    def build_filter_chain(self, threshold, pix_fmt, **options):
        rng = options.get("range", 16)
        blur = options.get("blur", True)
        dither = options.get("dither", 2)
        deflicker = options.get("deflicker", False)
        max_quality = options.get("max_quality", False)
        denoise = options.get("denoise", "off")
        colour_safe = options.get("colour_safe", False)
        denoise_filters = {"light": "hqdn3d=2:1:2:3", "medium": "hqdn3d=4:3:6:6"}
        chain = ""
        if denoise in denoise_filters:
            chain += denoise_filters[denoise] + ","
        if deflicker:
            chain += "deflicker,"
        if max_quality or colour_safe:
            chain += "format=yuv444p16le,"
        chroma_threshold = str(float(threshold) * 0.60) if colour_safe else threshold
        try:
            dither_amount = max(0, int(dither))
        except (TypeError, ValueError):
            dither_amount = 2
        chain += (f"deband=1thr={threshold}:2thr={chroma_threshold}:3thr={chroma_threshold}:"
                  f"range={rng}:blur={1 if blur else 0}")
        if dither_amount:
            # Temporal random dither is good at breaking up contour steps in a
            # delivery encode, but a high-bitrate ProRes master preserves it
            # as visible grain. A master profile can therefore disable it.
            flags = "p" if colour_safe else "t+u"
            chain += f",noise=alls={dither_amount}:allf={flags}"
        if colour_safe and dither_amount:
            # A fixed pattern avoids temporal randomization, while zscale's
            # error diffusion makes the final 16-bit → 10-bit reduction less
            # prone to coloured contour steps.
            chain += ",zscale=dither=error_diffusion"
        return chain + f",format={pix_fmt}"

    def availability(self, ffmpeg_path="ffmpeg"):
        return {"available": True, "reason": "Built-in CPU pipeline", "engine": self.engine_id}
