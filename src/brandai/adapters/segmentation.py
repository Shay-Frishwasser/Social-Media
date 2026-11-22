from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ForegroundSegmentationProvider(Protocol):
    model_name: str
    def cutout(self, source: Path, destination: Path) -> Path: ...


class BiRefNetProvider:
    """Reserved adapter; checkpoint loading is blocked pending final dataset-license sign-off."""
    model_name = "ZhengPeng7/BiRefNet"

    def cutout(self, source: Path, destination: Path) -> Path:
        raise RuntimeError("BiRefNet checkpoint is not enabled: commercial dataset provenance requires sign-off")
