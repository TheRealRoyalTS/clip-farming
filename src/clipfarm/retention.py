"""Delete clip videos by age and refuse to cut when the disk is nearly full.

Only `clip.mp4` is ever deleted. meta.json and publish.json stay, so thresholds can still be
tuned from past spikes and published URLs stay traceable. `approved` is never touched: that is
work someone decided to keep."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import RetentionConfig
from .queue import Queue


@dataclass(frozen=True)
class Removal:
    clip_id: str
    state: str
    bytes: int
    age_hours: float


def disk_ok(path: Path, min_free_gb: float) -> bool:
    return shutil.disk_usage(path).free >= min_free_gb * 1e9


def _limits(cfg: RetentionConfig) -> dict[str, float]:
    """state -> maximum age in seconds before the video is deleted."""
    return {
        "rejected": cfg.rejected_hours * 3600,
        "published": cfg.published_days * 86400,
        "pending": cfg.pending_days * 86400,
    }


def expire(queue: Queue, cfg: RetentionConfig, now: float, dry_run: bool = False) -> list[Removal]:
    removed: list[Removal] = []
    for state, limit in _limits(cfg).items():
        for clip_id in queue.list(state):
            clip_dir = queue.root / state / clip_id
            video, meta_file = clip_dir / "clip.mp4", clip_dir / "meta.json"
            if not video.is_file() or not meta_file.is_file():
                continue
            age = now - meta_file.stat().st_mtime  # meta.json is touched on every state change
            if age <= limit:
                continue
            removed.append(Removal(clip_id, state, video.stat().st_size, age / 3600))
            if dry_run:
                continue
            video.unlink()
            if (
                state == "pending"
            ):  # never decided: record that it timed out, not that it was rejected
                meta = json.loads(meta_file.read_text())
                meta["expired"] = True
                meta_file.write_text(json.dumps(meta, indent=2))
                queue.move(clip_id, "pending", "rejected")
    return removed
