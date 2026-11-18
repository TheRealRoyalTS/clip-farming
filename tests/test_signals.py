"""A chat spike only becomes a clip when independent signals agree."""

import shutil
import subprocess
from pathlib import Path

import pytest

from clipfarm.audio import AudioTracker, is_loud_spike, measure
from clipfarm.config import DetectorConfig
from clipfarm.detector import SpikeDetector

CFG = DetectorConfig()  # min_signals=2 by default


def warm(det, seconds=200, per_second=0.5):
    """Quiet background chat from many different people."""
    t, i = 0.0, 0
    while t < seconds:
        det.feed(t, "hello there", f"bg{i % 30}")
        t += 1 / per_second
        i += 1
    return seconds


def spam(det, start, n, user="spammer", text="wow"):
    return [r for i in range(n) if (r := det.feed(start + i * 0.1, text, user))]


def crowd(det, start, n, text=None):
    """n different chatters; each says something different unless `text` is given."""
    return [
        r
        for i in range(n)
        if (r := det.feed(start + i * 0.1, text or f"reaction number {i}", f"viewer{i}"))
    ]


def test_one_person_spamming_is_rejected_and_counted():
    det = SpikeDetector("c", CFG)
    t = warm(det)
    assert spam(det, t, 40) == []
    assert det.rejected == 1  # one spike, not one per message


def test_many_different_chatters_trigger_with_unique_signal():
    det = SpikeDetector("c", CFG)
    t = warm(det)
    trig = crowd(det, t, 40)
    assert len(trig) == 1
    assert trig[0].signals == ("rate", "unique") and trig[0].unique >= 8


def test_same_message_from_many_users_adds_same_signal():
    det = SpikeDetector("c", CFG)
    t = warm(det)
    trig = crowd(det, t, 40, text="NO WAY")
    assert "same" in trig[0].signals and trig[0].same >= 5


def test_same_message_from_one_user_is_not_a_same_signal():
    det = SpikeDetector("c", CFG)
    t = warm(det)
    assert spam(det, t, 40, text="NO WAY") == []


def test_loud_audio_rescues_a_spam_burst():
    det = SpikeDetector("c", CFG, audio_check=lambda now: True)
    t = warm(det)
    trig = spam(det, t, 40)
    assert trig and trig[0].signals == ("rate", "audio")


def test_quiet_audio_does_not_rescue_a_spam_burst():
    det = SpikeDetector("c", CFG, audio_check=lambda now: False)
    t = warm(det)
    assert spam(det, t, 40) == []


def test_audio_alone_without_chat_spike_never_triggers():
    det = SpikeDetector("c", CFG, audio_check=lambda now: True)
    t = warm(det)
    for i in range(30):  # normal chat while audio is loud
        assert det.feed(t + i * 2, "hello", f"u{i}") is None


def test_rejected_spike_does_not_start_the_cooldown():
    det = SpikeDetector("c", CFG)
    t = warm(det)
    assert spam(det, t, 40) == []
    assert crowd(det, t + 6, 40)  # a real reaction right afterwards still triggers


def test_min_signals_one_restores_chat_rate_only_behaviour():
    det = SpikeDetector("c", DetectorConfig(min_signals=1))
    t = warm(det)
    assert len(spam(det, t, 40)) == 1


def test_same_message_comparison_ignores_case_and_spacing():
    det = SpikeDetector("c", CFG)
    t = warm(det)
    texts = ["NO WAY", "no way", "No  Way", "no way ", "NO WAY!!"[:6], "no   way"]
    trig = [
        r for i in range(40) if (r := det.feed(t + i * 0.1, texts[i % len(texts)], f"viewer{i}"))
    ]
    assert trig and trig[0].same >= 5


# ---- audio ----


def test_loud_spike_needs_a_full_baseline():
    assert not is_loud_spike([-30.0] * 5 + [-10.0], jump_db=6)


def test_loud_spike_detected_against_median_baseline():
    quiet = [-30.0, -29.0, -31.0, -30.0, -30.5, -29.5, -30.0, -31.0, -30.0, -29.0]
    assert is_loud_spike(quiet + [-30.0, -22.0, -30.0], jump_db=6)  # +8 dB in the last 3
    assert not is_loud_spike(quiet + [-30.0, -26.0, -30.0], jump_db=6)  # only +4 dB


def test_one_old_loud_segment_in_the_baseline_does_not_hide_a_spike():
    base = [-30.0] * 4 + [-5.0] + [-30.0] * 5  # median ignores the single outlier
    assert is_loud_spike(base + [-30.0, -20.0, -30.0], jump_db=6)


def test_tracker_measures_each_segment_once_and_skips_the_newest(tmp_path):
    for e in (100, 106, 112):
        (tmp_path / f"{e}.ts").write_bytes(b"x")
    calls = []
    tr = AudioTracker(tmp_path, measure_fn=lambda p: calls.append(p.name) or -30.0)
    tr.update()
    tr.update()
    assert calls == ["100.ts", "106.ts"]  # 112 is still being written; nothing measured twice
    (tmp_path / "118.ts").write_bytes(b"x")
    tr.update()
    assert calls[-1] == "112.ts" and tr.levels() == [-30.0] * 3


def test_tracker_forgets_pruned_segments(tmp_path):
    for e in (100, 106, 112):
        (tmp_path / f"{e}.ts").write_bytes(b"x")
    tr = AudioTracker(tmp_path, measure_fn=lambda p: -30.0)
    tr.update()
    (tmp_path / "100.ts").unlink()
    tr.update()
    assert len(tr.levels()) == 1


def test_tracker_skips_unreadable_segments(tmp_path):
    for e in (100, 106):
        (tmp_path / f"{e}.ts").write_bytes(b"x")
    tr = AudioTracker(tmp_path, measure_fn=lambda p: None)
    tr.update()
    assert tr.levels() == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_measure_distinguishes_quiet_and_loud_audio_with_real_ffmpeg(tmp_path: Path):
    def make(name, gain):
        out = tmp_path / name
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"sine=frequency=440:duration=2,volume={gain}", "-f", "mpegts", str(out)],
            check=True,
        )  # fmt: skip
        return out

    quiet, loud = measure(make("q.ts", 0.05)), measure(make("l.ts", 0.8))
    assert quiet is not None and loud is not None
    assert loud - quiet > 15  # 0.8 vs 0.05 is ~24 dB
    assert measure(tmp_path / "missing.ts") is None
