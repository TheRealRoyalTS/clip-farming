"""Platform adapters. Register a factory here when an adapter exists."""

from __future__ import annotations

from collections.abc import Callable

from .base import Publisher
from .dryrun import DryRunPublisher

REGISTRY: dict[str, Callable[[], Publisher]] = {
    "dryrun": DryRunPublisher,
}


class UnknownPlatform(ValueError):
    pass


def build(names: tuple[str, ...] | list[str]) -> list[Publisher]:
    """Fail at startup, not mid-run, when config names a platform that has no adapter."""
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise UnknownPlatform(f"no adapter for {missing}; available: {sorted(REGISTRY)}")
    return [REGISTRY[n]() for n in names]
