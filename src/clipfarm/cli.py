from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import chat, clipper, recorder, retention
from .audio import AudioTracker
from .config import Config, channel_from, load
from .detector import SpikeDetector
from .pipeline import publish_approved
from .publishers import build as build_publishers
from .queue import Queue
from .quota import DailyQuota


async def _watch(cfg: Config, channel: str, queue: Queue) -> None:
    buf = cfg.data_dir / "buffer" / channel
    rec = asyncio.create_task(
        recorder.record(channel, buf, cfg.clip.buffer_seconds, cfg.stream.quality)
    )
    tracker = AudioTracker(buf)
    det = SpikeDetector(
        channel, cfg.detector, audio_check=lambda now: tracker.spike(cfg.detector.audio_jump_db)
    )
    pending: set[asyncio.Task] = set()

    async def make_clip(at: float, meta: dict) -> None:
        await asyncio.sleep(cfg.clip.post_seconds + 3)  # wait for post-roll to be written
        if not retention.disk_ok(cfg.data_dir, cfg.retention.min_free_gb):
            print(
                f"[{channel}] DISK GUARD: under {cfg.retention.min_free_gb} GB free, clip skipped"
            )
            return
        clip_id = f"{channel}-{int(at)}"
        tmp = cfg.data_dir / "tmp" / f"{clip_id}.mp4"
        try:
            await asyncio.to_thread(
                clipper.cut, buf, at, cfg.clip.pre_seconds, cfg.clip.post_seconds, tmp
            )
        except (clipper.NoFootage, OSError) as e:
            print(f"[{channel}] clip failed: {e}")
            return
        queue.add(clip_id, tmp, meta)
        print(f"[{channel}] queued {clip_id}")

    seen = 0
    started = time.time()

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(30)
            segs = len(list(buf.glob("*.ts")))
            warm = time.time() - started >= cfg.detector.baseline_seconds
            state = "watching" if warm else "warming up"
            print(
                f"[{channel}] {state}: {seen} chat msgs total, {segs} segments buffered, "
                f"{det.rejected} chat spike(s) rejected (single signal)"
            )

    async def audio_loop() -> None:
        while True:
            await asyncio.to_thread(tracker.update)
            await asyncio.sleep(3)

    beat = asyncio.create_task(heartbeat())
    audio = asyncio.create_task(audio_loop())
    print(
        f"[{channel}] started (quality {cfg.stream.quality}); "
        f"first spike possible after {cfg.detector.baseline_seconds:.0f}s"
    )
    try:
        async for msg in chat.read_chat(channel):
            seen += 1
            if trig := det.feed(msg.ts, msg.text, msg.user):
                print(
                    f"[{channel}] spike: {trig.rate:.0f} vs baseline {trig.baseline_mean:.1f}, "
                    f"signals: {'+'.join(trig.signals)} ({trig.unique} chatters, {trig.same}x same)"
                )
                meta = {"channel": channel, "at": trig.at, "rate": trig.rate,
                        "baseline_mean": trig.baseline_mean, "signals": list(trig.signals),
                        "unique": trig.unique, "same": trig.same}  # fmt: skip
                task = asyncio.create_task(make_clip(trig.at, meta))
                pending.add(task)
                task.add_done_callback(pending.discard)
    finally:
        rec.cancel()
        beat.cancel()
        audio.cancel()


def _report(removed: list[retention.Removal], dry_run: bool) -> None:
    verb = "would delete" if dry_run else "deleted"
    for r in removed:
        print(f"{verb} {r.clip_id} ({r.state}, {r.bytes / 1e6:.1f} MB, {r.age_hours:.0f} h old)")
    if removed:
        print(f"{verb} {sum(r.bytes for r in removed) / 1e6:.1f} MB in {len(removed)} clip(s)")


async def _retention_loop(cfg: Config, queue: Queue) -> None:
    while True:
        _report(retention.expire(queue, cfg.retention, time.time()), dry_run=False)
        await asyncio.sleep(cfg.retention.interval_minutes * 60)


async def _run(cfg: Config) -> None:
    queue = Queue(cfg.data_dir / "queue")
    n = recorder.clean_stale(cfg.data_dir / "tmp", older_than=600, now=time.time())
    if n:
        print(f"removed {n} stale file(s) from {cfg.data_dir / 'tmp'}")
    await asyncio.gather(
        _retention_loop(cfg, queue), *(_watch(cfg, c, queue) for c in cfg.channels)
    )


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # log lines must appear when piped or under systemd
    p = argparse.ArgumentParser(prog="clipfarm")
    p.add_argument("-c", "--config", type=Path, default=Path("config.toml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="watch configured channels (or the links given)")
    run.add_argument("links", nargs="*", help="Twitch links/names; replaces the config list")
    sub.add_parser("publish", help="publish approved clips to all configured platforms")
    cl = sub.add_parser("cleanup", help="delete clip videos past their retention time")
    cl.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    ls = sub.add_parser("list", help="list clips in a state")
    ls.add_argument("state", nargs="?", default="pending")
    for name in ("approve", "reject"):
        sub.add_parser(name).add_argument("clip_id")
    args = p.parse_args()

    cfg = load(args.config)
    queue = Queue(cfg.data_dir / "queue")
    if args.cmd == "run":
        if args.links:
            cfg = replace(cfg, channels=tuple(channel_from(x) for x in args.links))
        asyncio.run(_run(cfg))
    elif args.cmd == "publish":
        rep = publish_approved(
            queue,
            build_publishers(cfg.publish.platforms),
            DailyQuota(cfg.data_dir / "state" / "quota.json", cfg.publish.daily_limits),
            title_template=cfg.publish.title_template,
            description_template=cfg.publish.description_template,
            attempts=cfg.publish.attempts,
            base_delay=cfg.publish.base_delay,
        )
        for clip, plat in rep.done:
            print(f"published {clip} -> {plat}")
        for clip, plat, why in rep.failed + rep.skipped:
            print(f"NOT published {clip} -> {plat}: {why}")
        print(f"{len(rep.completed_clips)} clip(s) fully published")
    elif args.cmd == "cleanup":
        _report(retention.expire(queue, cfg.retention, time.time(), args.dry_run), args.dry_run)
    elif args.cmd == "list":
        for cid in queue.list(args.state):
            m = queue.meta(cid, args.state)
            print(cid, time.strftime("%F %T", time.localtime(m["at"])), f"rate={m['rate']:.0f}")
    else:
        getattr(queue, args.cmd)(args.clip_id)
