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
        denoise_filters = {"light": "hqdn3d=2:1:2:3", "medium": "hqdn3d=4:3:6:6"}
        chain = ""
        if denoise in denoise_filters:
            chain += denoise_filters[denoise] + ","
        if deflicker:
            chain += "deflicker,"
        if max_quality:
            chain += "format=yuv444p16le,"
        chain += (f"deband=1thr={threshold}:2thr={threshold}:3thr={threshold}:"
                  f"range={rng}:blur={1 if blur else 0},noise=alls={dither}:allf=t+u")
        return chain + f",format={pix_fmt}"

    def availability(self, ffmpeg_path="ffmpeg"):
        return {"available": True, "reason": "Built-in CPU pipeline", "engine": self.engine_id}
