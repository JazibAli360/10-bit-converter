"""Pure output-size prediction helpers used by preflight and the UI."""


def estimate_export_bytes(info, mode, rate, settings, mode_kind):
    duration = info.get("dur") or 0
    if not duration:
        return 0
    kind = mode_kind(mode)
    if kind == "prores":
        return int((info.get("width") or 1920) * (info.get("height") or 1080) * 5.31 * (info.get("fps") or 30) * duration / 8)
    if rate == "Custom":
        return int((float(settings.get("target_mbps", 12)) * 1_000_000 + 192_000) * duration / 8)
    if rate == "Match source" and info.get("kbps"):
        return int(info["kbps"] * 1000 * duration / 8)
    if rate == "Quality (CRF)":
        source_size = info.get("size") or 0
        if not source_size and info.get("kbps"):
            source_size = int(info["kbps"] * 1000 * duration / 8)
        if source_size:
            return int(source_size * (1.05 if kind == "h264" else 0.78))
    return 0


__all__ = ["estimate_export_bytes"]
