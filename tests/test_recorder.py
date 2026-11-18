import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clipfarm.recorder import prune, recorder_command, segment_epoch


def touch(d, epoch):
    p = d / f"{epoch}.ts"
    p.write_bytes(b"x")
    return p


def test_segment_epoch(tmp_path):
    assert segment_epoch(touch(tmp_path, 1700000000)) == 1700000000


def test_prune_removes_only_segments_older_than_window(tmp_path):
    old, edge, new = touch(tmp_path, 100), touch(tmp_path, 880), touch(tmp_path, 990)
    assert prune(tmp_path, keep_seconds=120, now=1000) == 1
    assert not old.exists() and edge.exists() and new.exists()


def test_prune_ignores_other_files(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("keep")
    prune(tmp_path, 1, 10**10)
    assert other.exists()


def test_recorder_command_targets_channel_and_epoch_names(tmp_path):
    cmd = recorder_command("somechan", tmp_path)
    assert "twitch.tv/somechan" in cmd and "-strftime 1" in cmd and "%s.ts" in cmd


def test_recorder_command_uses_quality_list(tmp_path):
    assert "720p60,720p,best" in recorder_command("c", tmp_path, "720p60,720p,best")
    assert recorder_command("c", tmp_path).count(" best ") == 1  # default stays 'best'


def test_recorder_command_quotes_hostile_values(tmp_path):
    cmd = recorder_command("c", tmp_path, "best; touch /tmp/pwned")
    assert "'best; touch /tmp/pwned'" in cmd  # one shell word, not a second command


def test_clean_stale_removes_only_old_files(tmp_path):
    from clipfarm.recorder import clean_stale

    old, fresh = tmp_path / "old.mp4", tmp_path / "fresh.mp4"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    os.utime(old, (100, 100))
    os.utime(fresh, (1990, 1990))
    assert clean_stale(tmp_path, older_than=600, now=2000) == 1
    assert not old.exists() and fresh.exists()
    assert clean_stale(tmp_path / "missing", older_than=600, now=2000) == 0


def test_segments_are_not_shorter_than_one_twitch_block():
    # Guarantee: no segment is lost to a filename collision. Blocks arrive in bursts of ~6 s, so
    # shorter segments would share a %s name and overwrite each other (measured: 16 s of 45 s kept).
    from clipfarm.recorder import SEGMENT_SECONDS

    assert SEGMENT_SECONDS >= 6
    assert f"-segment_time {SEGMENT_SECONDS} " in recorder_command("c", Path("/x"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_recorder_keeps_all_content_of_a_realtime_stream(tmp_path):
    """Real ffmpeg, real-time input (-re), same muxer settings as the recorder: the files must hold
    (almost) all 14 s that were fed in."""
    from clipfarm.recorder import SEGMENT_SECONDS

    out = tmp_path / "%s.ts"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=14",
         "-c:v", "libx264", "-g", "25", "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
         "-strftime", "1", "-reset_timestamps", "1", str(out)],
        check=True,
    )  # fmt: skip
    total = sum(
        float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
            check=True, capture_output=True, text=True,
        ).stdout)
        for p in tmp_path.glob("*.ts")
    )  # fmt: skip
    assert total >= 13.0
