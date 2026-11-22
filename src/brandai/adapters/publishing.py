from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PublishRequest:
    brand_id: str
    platform: str
    caption: str
    media_paths: tuple[Path, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    provider: str
    external_id: str | None
    status: str


class SocialPublisher(Protocol):
    """Future OAuth publishers implement this without entering domain logic."""
    name: str
    async def publish(self, request: PublishRequest) -> DeliveryReceipt: ...


class PackageDelivery(Protocol):
    name: str
    async def deliver(self, request: PublishRequest, recipient: str | None = None) -> DeliveryReceipt: ...
