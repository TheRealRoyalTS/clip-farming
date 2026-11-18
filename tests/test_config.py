from pathlib import Path

from clipfarm.config import load

EXAMPLE = Path(__file__).parent.parent / "config.example.toml"


def test_example_config_loads():
    cfg = load(EXAMPLE)
    assert cfg.channels == ("example_channel",)
    assert cfg.clip.pre_seconds == 15 and cfg.clip.post_seconds == 20
    assert "KEKW" in cfg.detector.hype_terms


def test_defaults_and_channel_lowercasing(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[[channels]]\nname = "MixedCase"\n')
    cfg = load(f)
    assert cfg.channels == ("mixedcase",)
    assert cfg.detector.sigma == 3.0


def test_publish_config_defaults_and_overrides(tmp_path):
    assert load(EXAMPLE).publish.platforms == ("dryrun",)
    f = tmp_path / "c.toml"
    f.write_text(
        '[publish]\nplatforms = ["youtube","tiktok"]\n[publish.daily_limits]\nyoutube = 6\n'
    )
    cfg = load(f)
    assert cfg.publish.platforms == ("youtube", "tiktok")
    assert cfg.publish.daily_limits == {"youtube": 6}
    assert cfg.publish.attempts == 3


import pytest

from clipfarm.config import channel_from


@pytest.mark.parametrize(
    "value",
    [
        "https://www.twitch.tv/SomeChan",
        "http://twitch.tv/somechan/",
        "twitch.tv/somechan?sr=a",
        "https://m.twitch.tv/somechan",
        "#SomeChan",
        "somechan",
    ],
)
def test_channel_from_accepts_links_and_names(value):
    assert channel_from(value) == "somechan"


@pytest.mark.parametrize("value", ["https://youtube.com/@x", "https://twitch.tv/", "two words", ""])
def test_channel_from_rejects_other_input(value):
    with pytest.raises(ValueError):
        channel_from(value)


def test_config_accepts_url_entries(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[[channels]]\nurl = "https://www.twitch.tv/Foo"\n[[channels]]\nname = "Bar"\n')
    assert load(f).channels == ("foo", "bar")


def test_stream_quality_default_and_override(tmp_path):
    assert load(EXAMPLE).stream.quality == "720p60,720p,best"
    f = tmp_path / "c.toml"
    f.write_text('[stream]\nquality = "480p30,best"\n')
    assert load(f).stream.quality == "480p30,best"
    f.write_text("")
    assert load(f).stream.quality == "720p60,720p,best"


def test_retention_defaults_and_overrides(tmp_path):
    r = load(EXAMPLE).retention
    assert (r.rejected_hours, r.published_days, r.pending_days, r.min_free_gb) == (24, 7, 3, 2.0)
    f = tmp_path / "c.toml"
    f.write_text("[retention]\nrejected_hours = 1\n")
    assert load(f).retention.rejected_hours == 1 and load(f).retention.pending_days == 3
