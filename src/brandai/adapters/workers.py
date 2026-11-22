from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorkerLease:
    base_url: str
    worker_id: str
    capabilities: frozenset[str]


class GPUWorkerProvider(Protocol):
    """Lease boundary shared by the local GPU and future ephemeral cloud workers."""
    def lease(self, capability: str) -> AbstractContextManager[WorkerLease]: ...
