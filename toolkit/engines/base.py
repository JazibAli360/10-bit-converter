"""Small contract shared by current and future conversion engines."""

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol


@dataclass(frozen=True)
class EngineCapabilities:
    processed_sample: bool
    gradient_analysis: bool
    local_only: bool
    requires_gpu_backend: bool = False


class ConversionEngine(Protocol):
    engine_id: str
    display_name: str
    capabilities: EngineCapabilities
    strength_thresholds: Mapping[str, Optional[str]]

    def threshold_for(self, strength: str, custom: str) -> str:
        ...

    def build_filter_chain(self, threshold: str, pix_fmt: str, **options: object) -> str:
        """Return a safe FFmpeg filter chain for the selected engine."""
        ...

    def availability(self, ffmpeg_path: str = "ffmpeg") -> dict:
        """Describe whether this engine can be selected in this installation."""
        ...
