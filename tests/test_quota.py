from datetime import date

from clipfarm.quota import DailyQuota


def test_unlimited_platform_always_allowed(tmp_path):
    q = DailyQuota(tmp_path / "q.json", {})
    for _ in range(50):
        q.record("tiktok")
    assert q.allowed("tiktok")


def test_limit_is_enforced(tmp_path):
    q = DailyQuota(tmp_path / "q.json", {"youtube": 2})
    assert q.allowed("youtube")
    q.record("youtube")
    q.record("youtube")
    assert not q.allowed("youtube") and q.used("youtube") == 2


def test_counter_persists_across_instances(tmp_path):
    DailyQuota(tmp_path / "q.json", {"youtube": 1}).record("youtube")
    assert not DailyQuota(tmp_path / "q.json", {"youtube": 1}).allowed("youtube")


def test_budget_resets_on_new_day(tmp_path):
    day = {"d": date(2026, 11, 18)}
    q = DailyQuota(tmp_path / "q.json", {"youtube": 1}, today=lambda: day["d"])
    q.record("youtube")
    assert not q.allowed("youtube")
    day["d"] = date(2026, 11, 19)
    assert q.allowed("youtube") and q.used("youtube") == 0


def test_platforms_are_counted_separately(tmp_path):
    q = DailyQuota(tmp_path / "q.json", {"youtube": 1, "tiktok": 1})
    q.record("youtube")
    assert q.allowed("tiktok")
