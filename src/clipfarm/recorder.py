"""Rolling recorder: streamlink -> ffmpeg writes 2 s segments named <epoch>.ts and prunes old ones."""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

# Twitch delivers video in ~6 s blocks that arrive at once. Segment files are named by the wall-clock
# second (%s), so shorter segments cut from one block share a name and overwrite each other: with
# 2 s segments only 16 s of 45 s survived on a live channel; with 6 s all 48 s did.
SEGMENT_SECONDS = 6


def segment_epoch(path: Path) -> int:
    return int(path.stem)


def prune(buffer_dir: Path, keep_seconds: float, now: float) -> int:
    removed = 0
    for p in buffer_dir.glob("*.ts"):
        if segment_epoch(p) < now - keep_seconds:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def recorder_command(channel: str, buffer_dir: Path, quality: str = "best") -> str:
    out = buffer_dir / "%s.ts"
    return (
        f"streamlink --stdout --twitch-disable-ads https://twitch.tv/{shlex.quote(channel)} "
        f"{shlex.quote(quality)} "
        f"| ffmpeg -loglevel error -i - -c copy -f segment -segment_time {SEGMENT_SECONDS} "
        f"-strftime 1 -reset_timestamps 1 {shlex.quote(str(out))}"
    )


async def record(
    channel: str, buffer_dir: Path, keep_seconds: float, quality: str = "best"
) -> None:
    """Run until cancelled; restart when the stream drops or has not started yet."""
    buffer_dir.mkdir(parents=True, exist_ok=True)

    async def janitor() -> None:
        while True:
            prune(buffer_dir, keep_seconds, time.time())
            await asyncio.sleep(10)

    task = asyncio.create_task(janitor())
    try:
        while True:
            proc = await asyncio.create_subprocess_shell(
                recorder_command(channel, buffer_dir, quality), stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            print(f"[{channel}] recorder stopped (exit {proc.returncode}); offline? retry in 15s")
            await asyncio.sleep(15)  # offline or dropped; retry
    finally:
        task.cancel()


def clean_stale(directory: Path, older_than: float, now: float) -> int:
    """Delete leftovers of cuts that never finished. The age guard keeps a cut that another
    instance is writing right now."""
    removed = 0
    if directory.is_dir():
        for p in directory.iterdir():
            if p.is_file() and p.stat().st_mtime < now - older_than:
                p.unlink(missing_ok=True)
                removed += 1
    return removed
