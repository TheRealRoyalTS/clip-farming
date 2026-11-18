from __future__ import annotations

from .base import PublishRequest, PublishResult


class DryRunPublisher:
    """Publishes nothing. Lets the whole approve -> publish path run without credentials."""

    name = "dryrun"

    def __init__(self) -> None:
        self.calls: list[PublishRequest] = []

    def publish(self, req: PublishRequest) -> PublishResult:
        self.calls.append(req)
        return PublishResult(remote_id=f"dryrun-{req.video.parent.name}", url=None)
