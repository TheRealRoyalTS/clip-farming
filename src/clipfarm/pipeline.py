"""Publish approved clips to every configured platform.

Per-clip state lives in publish.json inside the clip directory, so a re-run only retries the
platforms that are not done yet and never uploads the same clip twice to the same platform."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .publishers.base import (
    AuthError,
    PermanentError,
    Publisher,
    PublishError,
    PublishRequest,
    RateLimited,
    TransientError,
)
from .queue import Queue
from .quota import DailyQuota
from .retry import call_with_retry

STATE_FILE = "publish.json"


@dataclass
class Report:
    done: list[tuple[str, str]] = field(default_factory=list)  # (clip, platform)
    failed: list[tuple[str, str, str]] = field(default_factory=list)  # (clip, platform, why)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)
    completed_clips: list[str] = field(default_factory=list)


def build_request(
    clip_dir: Path, meta: dict, title_template: str, description_template: str
) -> PublishRequest:
    fields = {"channel": meta["channel"]}
    return PublishRequest(
        video=clip_dir / "clip.mp4",
        title=title_template.format(**fields)[:100],  # YouTube titles cap at 100 chars
        description=description_template.format(**fields),
    )


def read_state(clip_dir: Path) -> dict:
    try:
        return json.loads((clip_dir / STATE_FILE).read_text())
    except FileNotFoundError:
        return {}


def write_state(clip_dir: Path, state: dict) -> None:
    tmp = clip_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, clip_dir / STATE_FILE)


def publish_approved(
    queue: Queue,
    publishers: list[Publisher],
    quota: DailyQuota,
    *,
    title_template: str = "{channel} clip #shorts",
    description_template: str = "Clip from twitch.tv/{channel}",
    attempts: int = 3,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> Report:
    report = Report()
    blocked: dict[str, str] = {}  # platform -> reason; skipped for the rest of this run

    for clip_id in queue.list("approved"):
        clip_dir = queue.root / "approved" / clip_id
        req = build_request(
            clip_dir, queue.meta(clip_id, "approved"), title_template, description_template
        )
        state = read_state(clip_dir)

        for pub in publishers:
            if state.get(pub.name, {}).get("status") == "done":
                continue
            if pub.name in blocked:
                report.skipped.append((clip_id, pub.name, blocked[pub.name]))
                continue
            if not quota.allowed(pub.name):
                blocked[pub.name] = "daily quota reached"
                report.skipped.append((clip_id, pub.name, blocked[pub.name]))
                continue

            try:
                result = call_with_retry(
                    lambda p=pub, r=req: p.publish(r),
                    attempts=attempts,
                    base_delay=base_delay,
                    sleep=sleep,
                )
            except RateLimited as e:
                blocked[pub.name] = f"rate limited: {e}"
                report.skipped.append((clip_id, pub.name, blocked[pub.name]))
                continue
            except AuthError as e:
                blocked[pub.name] = f"auth: {e}"  # every other clip would fail the same way
                state[pub.name] = {"status": "failed", "error": blocked[pub.name], "at": now()}
                report.failed.append((clip_id, pub.name, blocked[pub.name]))
            except (PermanentError, TransientError, PublishError) as e:
                state[pub.name] = {"status": "failed", "error": str(e), "at": now()}
                report.failed.append((clip_id, pub.name, str(e)))
            else:
                quota.record(pub.name)
                state[pub.name] = {
                    "status": "done",
                    "remote_id": result.remote_id,
                    "url": result.url,
                    "at": now(),
                }
                report.done.append((clip_id, pub.name))
            write_state(clip_dir, state)

        if all(state.get(p.name, {}).get("status") == "done" for p in publishers):
            queue.move(clip_id, "approved", "published")
            report.completed_clips.append(clip_id)
    return report
