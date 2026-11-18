"""Contract every platform adapter implements, and the error model the pipeline reacts to."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class PublishRequest:
    video: Path
    title: str
    description: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishResult:
    remote_id: str
    url: str | None = None


class PublishError(Exception):
    """Base class. Adapters raise a subclass so the pipeline knows what to do next."""


class TransientError(PublishError):
    """Network blip, 5xx: retry with backoff."""


class RateLimited(PublishError):
    """Platform said slow down or quota is spent: stop using this platform for this run."""

    def __init__(self, message: str = "rate limited", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AuthError(PublishError):
    """Missing, expired or revoked credentials: a human has to fix it."""


class PermanentError(PublishError):
    """The platform rejected this clip (bad format, policy): retrying the same call will not help."""


class Publisher(Protocol):
    name: str

    def publish(self, req: PublishRequest) -> PublishResult: ...
