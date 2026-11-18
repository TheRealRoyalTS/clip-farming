"""Platform adapters. Register a factory here when an adapter exists."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import YouTubeConfig
from .base import Publisher
from .dryrun import DryRunPublisher
from .youtube import YouTubePublisher

if TYPE_CHECKING:  # imported for the annotation only: credentials imports this package
    from ..credentials import TokenStore


@dataclass(frozen=True)
class PublishContext:
    tokens: TokenStore
    youtube: YouTubeConfig


def _youtube(ctx: PublishContext | None) -> Publisher:
    if ctx is None:
        raise ValueError("the youtube adapter needs a PublishContext (token store and settings)")
    return YouTubePublisher(ctx.tokens, ctx.youtube)


REGISTRY: dict[str, Callable[[PublishContext | None], Publisher]] = {
    "dryrun": lambda ctx: DryRunPublisher(),
    "youtube": _youtube,
}


class UnknownPlatform(ValueError):
    pass


def build(names: tuple[str, ...] | list[str], ctx: PublishContext | None = None) -> list[Publisher]:
    """Fail at startup, not mid-run, when config names a platform that has no adapter."""
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise UnknownPlatform(f"no adapter for {missing}; available: {sorted(REGISTRY)}")
    return [REGISTRY[n](ctx) for n in names]
