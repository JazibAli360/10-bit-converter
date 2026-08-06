"""Conversion engine registry.

Only the honest FFmpeg deband engine ships today. Future restoration engines
can be added here without coupling model download/runtime concerns to the UI.
"""

from .ffmpeg_deband import FFmpegDebandEngine
from .libplacebo_deband import LibplaceboDebandEngine

DEFAULT_ENGINE = FFmpegDebandEngine()
LIBPLACEBO_ENGINE = LibplaceboDebandEngine()


def engine_catalog(ffmpeg_path="ffmpeg", libplacebo_path=None):
    """Return engine metadata for capability-aware UI and diagnostics."""
    engines = (DEFAULT_ENGINE, LIBPLACEBO_ENGINE)
    paths = {DEFAULT_ENGINE.engine_id: ffmpeg_path,
             LIBPLACEBO_ENGINE.engine_id: libplacebo_path or ffmpeg_path}
    return [
        {
            "id": engine.engine_id,
            "name": engine.display_name,
            "capabilities": engine.capabilities.__dict__,
            **engine.availability(paths[engine.engine_id]),
        }
        for engine in engines
    ]


def requested_engine(engine_id, ffmpeg_path="ffmpeg", libplacebo_path=None):
    """Return an engine only when the requested ID is available."""
    candidates = {DEFAULT_ENGINE.engine_id: DEFAULT_ENGINE,
                  LIBPLACEBO_ENGINE.engine_id: LIBPLACEBO_ENGINE}
    engine = candidates.get(engine_id or DEFAULT_ENGINE.engine_id)
    if engine is None:
        return None, "Unknown conversion engine."
    path = libplacebo_path if engine.engine_id == LIBPLACEBO_ENGINE.engine_id and libplacebo_path else ffmpeg_path
    status = engine.availability(path)
    return (engine, "") if status.get("available") else (None, status.get("reason", "Engine unavailable."))

__all__ = ["DEFAULT_ENGINE", "LIBPLACEBO_ENGINE", "FFmpegDebandEngine",
           "LibplaceboDebandEngine", "engine_catalog", "requested_engine"]
