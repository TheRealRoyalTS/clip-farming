import pytest

from clipfarm.pipeline import publish_approved, read_state
from clipfarm.publishers import UnknownPlatform, build
from clipfarm.publishers.base import (
    AuthError,
    PermanentError,
    PublishResult,
    RateLimited,
    TransientError,
)
from clipfarm.publishers.dryrun import DryRunPublisher
from clipfarm.queue import Queue
from clipfarm.quota import DailyQuota


class Fake:
    """Scripted publisher: each call pops the next outcome (exception instance or None = success)."""

    def __init__(self, name, outcomes=()):
        self.name, self.outcomes, self.calls = name, list(outcomes), []

    def publish(self, req):
        self.calls.append(req.video.parent.name)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome:
            raise outcome
        return PublishResult(
            remote_id=f"{self.name}-{len(self.calls)}", url=f"https://{self.name}/x"
        )


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q")


def approved(q, tmp_path, *ids):
    for cid in ids:
        f = tmp_path / f"{cid}.mp4"
        f.write_bytes(b"v")
        q.add(cid, f, {"channel": "somechan", "at": 1.0, "rate": 9.0})
        q.approve(cid)


def run(q, tmp_path, pubs, limits=None, **kw):
    quota = DailyQuota(tmp_path / "quota.json", limits or {})
    return publish_approved(q, pubs, quota, sleep=lambda s: None, **kw)


def test_success_on_all_platforms_moves_clip_to_published(q, tmp_path):
    approved(q, tmp_path, "a")
    yt, tt = Fake("youtube"), Fake("tiktok")
    rep = run(q, tmp_path, [yt, tt])
    assert rep.completed_clips == ["a"]
    assert q.list("approved") == [] and q.list("published") == ["a"]
    assert read_state(q.root / "published" / "a")["youtube"]["url"] == "https://youtube/x"


def test_partial_failure_keeps_clip_approved_and_rerun_only_retries_failed_platform(q, tmp_path):
    approved(q, tmp_path, "a")
    yt, tt = Fake("youtube"), Fake("tiktok", [PermanentError("bad codec")])
    rep = run(q, tmp_path, [yt, tt])
    assert rep.failed == [("a", "tiktok", "bad codec")]
    assert q.list("approved") == ["a"]

    rep2 = run(q, tmp_path, [yt, tt])
    assert yt.calls == ["a"], "youtube must not be uploaded twice"
    assert tt.calls == ["a", "a"]
    assert q.list("published") == ["a"] and rep2.completed_clips == ["a"]


def test_transient_error_is_retried_inside_one_run(q, tmp_path):
    approved(q, tmp_path, "a")
    yt = Fake("youtube", [TransientError("503"), TransientError("503")])
    run(q, tmp_path, [yt], attempts=3)
    assert len(yt.calls) == 3 and q.list("published") == ["a"]


def test_rate_limit_blocks_platform_for_rest_of_run_without_marking_failed(q, tmp_path):
    approved(q, tmp_path, "a", "b")
    yt = Fake("youtube", [RateLimited("slow down")])
    rep = run(q, tmp_path, [yt])
    assert yt.calls == ["a"], "no second call after rate limit"
    assert [s[:2] for s in rep.skipped] == [("a", "youtube"), ("b", "youtube")]
    assert rep.failed == [] and q.list("approved") == ["a", "b"]
    assert read_state(q.root / "approved" / "a") == {}


def test_auth_error_stops_that_platform_but_not_the_others(q, tmp_path):
    approved(q, tmp_path, "a", "b")
    yt, tt = Fake("youtube", [AuthError("token revoked")]), Fake("tiktok")
    rep = run(q, tmp_path, [yt, tt])
    assert yt.calls == ["a"] and tt.calls == ["a", "b"]
    assert rep.failed[0][:2] == ("a", "youtube")
    assert q.list("approved") == ["a", "b"]  # still waiting on youtube


def test_daily_quota_stops_uploads_and_leaves_rest_for_tomorrow(q, tmp_path):
    approved(q, tmp_path, "a", "b", "c")
    yt = Fake("youtube")
    rep = run(q, tmp_path, [yt], limits={"youtube": 2})
    assert yt.calls == ["a", "b"]
    assert q.list("published") == ["a", "b"] and q.list("approved") == ["c"]
    assert rep.skipped == [("c", "youtube", "daily quota reached")]


def test_failed_upload_does_not_consume_quota(q, tmp_path):
    approved(q, tmp_path, "a")
    run(q, tmp_path, [Fake("youtube", [PermanentError("x")])], limits={"youtube": 1})
    assert DailyQuota(tmp_path / "quota.json", {"youtube": 1}).allowed("youtube")


def test_title_and_description_credit_the_channel(q, tmp_path):
    approved(q, tmp_path, "a")
    seen = []

    class Spy(Fake):
        def publish(self, req):
            seen.append(req)
            return super().publish(req)

    run(q, tmp_path, [Spy("youtube")])
    assert seen[0].title == "somechan clip #shorts"
    assert "twitch.tv/somechan" in seen[0].description
    assert seen[0].video.name == "clip.mp4"


def test_title_is_capped_at_100_chars(q, tmp_path):
    approved(q, tmp_path, "a")
    seen = []

    class Spy(Fake):
        def publish(self, req):
            seen.append(req)
            return super().publish(req)

    run(q, tmp_path, [Spy("youtube")], title_template="{channel} " + "x" * 200)
    assert len(seen[0].title) == 100


def test_nothing_approved_is_a_noop(q, tmp_path):
    assert run(q, tmp_path, [Fake("youtube")]).done == []


def test_dryrun_publisher_runs_the_full_path(q, tmp_path):
    approved(q, tmp_path, "a")
    d = DryRunPublisher()
    run(q, tmp_path, [d])
    assert len(d.calls) == 1 and q.list("published") == ["a"]


def test_registry_rejects_unknown_platform_at_startup():
    assert [p.name for p in build(["dryrun"])] == ["dryrun"]
    with pytest.raises(UnknownPlatform, match="tiktok"):
        build(["dryrun", "tiktok"])
