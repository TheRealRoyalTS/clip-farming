import json
import os
import time
from pathlib import Path

import pytest

from clipfarm.config import RetentionConfig
from clipfarm.queue import Queue
from clipfarm.retention import disk_ok, expire

CFG = RetentionConfig()  # rejected 24 h, published 7 d, pending 3 d
H = 3600
NOW = 2_000_000_000.0


def add(q: Queue, tmp_path: Path, cid: str, state: str, age_hours: float) -> Path:
    f = tmp_path / f"{cid}.mp4"
    f.write_bytes(b"x" * 1000)
    q.add(cid, f, {"channel": "c", "at": 1.0, "rate": 9.0})
    if state != "pending":
        q.move(cid, "pending", state)
    d = q.root / state / cid
    os.utime(d / "meta.json", (NOW - age_hours * H,) * 2)
    return d


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q")


def test_rejected_video_is_deleted_after_24h_but_meta_survives(q, tmp_path):
    d = add(q, tmp_path, "r", "rejected", age_hours=25)
    out = expire(q, CFG, NOW)
    assert [(r.clip_id, r.state, r.bytes) for r in out] == [("r", "rejected", 1000)]
    assert not (d / "clip.mp4").exists() and (d / "meta.json").exists()


def test_young_clips_are_kept(q, tmp_path):
    d = add(q, tmp_path, "r", "rejected", age_hours=23)
    assert expire(q, CFG, NOW) == [] and (d / "clip.mp4").exists()


def test_published_video_deleted_after_7_days_publish_json_survives(q, tmp_path):
    d = add(q, tmp_path, "p", "published", age_hours=24 * 7 + 1)
    (d / "publish.json").write_text(json.dumps({"youtube": {"status": "done"}}))
    expire(q, CFG, NOW)
    assert not (d / "clip.mp4").exists() and (d / "publish.json").exists()


def test_approved_clips_are_never_deleted_however_old(q, tmp_path):
    d = add(q, tmp_path, "a", "approved", age_hours=24 * 365)
    assert expire(q, CFG, NOW) == [] and (d / "clip.mp4").exists()


def test_expired_pending_clip_is_moved_to_rejected_and_marked(q, tmp_path):
    add(q, tmp_path, "x", "pending", age_hours=24 * 3 + 1)
    expire(q, CFG, NOW)
    assert q.list("pending") == [] and q.list("rejected") == ["x"]
    assert q.meta("x", "rejected")["expired"] is True
    assert not (q.root / "rejected" / "x" / "clip.mp4").exists()


def test_dry_run_reports_but_deletes_nothing(q, tmp_path):
    d = add(q, tmp_path, "r", "rejected", age_hours=100)
    assert len(expire(q, CFG, NOW, dry_run=True)) == 1
    assert (d / "clip.mp4").exists() and q.list("rejected") == ["r"]


def test_entry_without_video_is_skipped(q, tmp_path):
    d = add(q, tmp_path, "r", "rejected", age_hours=100)
    (d / "clip.mp4").unlink()
    assert expire(q, CFG, NOW) == []  # already gone: not reported twice


def test_second_run_deletes_nothing_more(q, tmp_path):
    add(q, tmp_path, "r", "rejected", age_hours=100)
    assert len(expire(q, CFG, NOW)) == 1 and expire(q, CFG, NOW) == []


def test_state_change_restarts_the_age(q, tmp_path):
    add(q, tmp_path, "c", "pending", age_hours=24 * 10)  # old, but rejected just now
    q.reject("c")
    assert expire(q, CFG, time.time()) == []  # the 24 h in 'rejected' have just begun


def test_disk_guard(monkeypatch, tmp_path):
    import shutil
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100e9, 98e9, 1.5e9))
    assert not disk_ok(tmp_path, 2.0)
    assert disk_ok(tmp_path, 1.0)
