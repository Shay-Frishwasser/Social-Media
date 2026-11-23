from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from brandai.config import RuntimeSettings

from .workers import GPUWorkerProvider, WorkerLease


class ForegroundSegmentationProvider(Protocol):
    model_name: str
    def cutout(self, source: Path, destination: Path) -> Path: ...


PRIMARY_SEGMENTATION_PROVIDER = "sam2"
SAM2_CONFIGS = {
    "sam2.1_hiera_tiny": "configs/sam2.1/sam2.1_hiera_t.yaml",
    "sam2.1_hiera_small": "configs/sam2.1/sam2.1_hiera_s.yaml",
}


@dataclass(frozen=True, slots=True)
class SAM2Settings:
    variant: str
    checkpoint_dir: Path

    def __post_init__(self) -> None:
        if self.variant not in SAM2_CONFIGS:
            raise ValueError("BRANDAI_SAM2_VARIANT must select SAM 2.1 tiny or small")

    @classmethod
    def from_runtime(cls, runtime: RuntimeSettings) -> SAM2Settings:
        return cls(os.environ.get("BRANDAI_SAM2_VARIANT", "sam2.1_hiera_tiny"),
                   runtime.directory("BRANDAI_SAM2_DIR", "models/segmentation/sam2"))

    @property
    def model_config(self) -> str:
        return SAM2_CONFIGS[self.variant]

    @property
    def checkpoint(self) -> Path:
        return self.checkpoint_dir / f"{self.variant}.pt"


class SAM2Runner(Protocol):
    """Run promptable segmentation on the leased worker and write a source-RGB RGBA cutout."""

    def __call__(self, lease: WorkerLease, settings: SAM2Settings, source: Path, destination: Path) -> Path: ...


class SAM2Provider:
    model_name = "facebookresearch/sam2"

    def __init__(self, settings: SAM2Settings, worker: GPUWorkerProvider, runner: SAM2Runner):
        self.settings, self.worker, self.runner = settings, worker, runner

    def cutout(self, source: Path, destination: Path) -> Path:
        with self.worker.lease("foreground_segmentation") as lease:
            return self.runner(lease, self.settings, source, destination)


class BiRefNetProvider:
    """Reserved adapter; checkpoint loading is blocked pending final dataset-license sign-off."""
    model_name = "ZhengPeng7/BiRefNet"

    def cutout(self, source: Path, destination: Path) -> Path:
        raise RuntimeError("BiRefNet checkpoint is not enabled: commercial dataset provenance requires sign-off")
