import shutil
import subprocess

import pytest

from clipfarm import clipper


def make_buffer(d, epochs):
    for e in epochs:
        (d / f"{e}.ts").write_bytes(b"x")


def test_select_segments_covers_window_with_partial_edges(tmp_path):
    make_buffer(tmp_path, range(1000, 1060, 2))
    segs = clipper.select_segments(tmp_path, start=1011, end=1031)
    epochs = [int(p.stem) for p in segs]
    assert epochs[0] == 1010  # segment 1010-1012 contains 1011
    assert epochs[-1] == 1030  # segment 1030-1032 contains 1031
    assert epochs == sorted(epochs)
    assert 1032 not in epochs and 1008 not in epochs


def test_select_segments_empty_when_window_not_buffered(tmp_path):
    make_buffer(tmp_path, [1000, 1002])
    assert clipper.select_segments(tmp_path, 5000, 5020) == []


def test_cut_raises_when_no_footage(tmp_path):
    with pytest.raises(clipper.NoFootage):
        clipper.cut(tmp_path, at=100, pre=15, post=20, out=tmp_path / "o" / "c.mp4")


def test_ffmpeg_args_offset_into_first_segment(tmp_path):
    args = clipper.ffmpeg_args(
        tmp_path / "l.txt", first_epoch=1010, start=1011, duration=35, out=tmp_path / "o.mp4"
    )
    assert args[args.index("-ss") + 1] == "1.00"
    assert args[args.index("-t") + 1] == "35.00"
    assert "1080:1920" in args[args.index("-filter_complex") + 1]


def test_ffmpeg_args_offset_never_negative(tmp_path):
    # window starts before the oldest buffered segment (stream just started)
    args = clipper.ffmpeg_args(
        tmp_path / "l.txt", first_epoch=1020, start=1005, duration=35, out=tmp_path / "o.mp4"
    )
    assert args[args.index("-ss") + 1] == "0.00"


def test_concat_list_uses_absolute_paths(tmp_path):
    make_buffer(tmp_path, [1000])
    text = clipper.concat_list(clipper.select_segments(tmp_path, 999, 1003))
    assert text == f"file '{(tmp_path / '1000.ts').resolve()}'\n"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_cut_produces_vertical_clip_of_requested_length(tmp_path):
    """Real ffmpeg: synthesize a 60 s landscape 'stream' as 2 s segments, cut 15 s before / 20 s after."""
    base = 1_700_000_000
    for i in range(30):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-c:a", "aac", "-shortest", "-f", "mpegts",
             str(tmp_path / f"{base + i * 2}.ts")],
            check=True,
        )  # fmt: skip
    out = clipper.cut(tmp_path, at=base + 30, pre=15, post=20, out=tmp_path / "out" / "clip.mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "default=nw=1", str(out)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    info = dict(line.split("=") for line in probe.split())
    assert (info["width"], info["height"]) == ("1080", "1920")
    assert abs(float(info["duration"]) - 35) < 1.5


def test_select_segments_with_real_6s_keyframe_segments(tmp_path):
    """Twitch keyframes land ~6 s apart, and `-c copy` only cuts there, so segments are 6 s long
    (gaps measured on a live channel: 6 6 6 6 ...). A segment that began before the window start
    but still covers it must be included, or the clip loses up to 6 s of pre-roll."""
    make_buffer(tmp_path, range(1000, 1060, 6))
    epochs = [int(p.stem) for p in clipper.select_segments(tmp_path, start=1015, end=1030)]
    assert epochs == [
        1012,
        1018,
        1024,
    ]  # 1012 covers 1012-1018 and contains 1015; 1030 starts at end


def test_select_segments_last_segment_is_assumed_one_keyframe_interval_long(tmp_path):
    make_buffer(tmp_path, [1000, 1006, 1012])  # newest segment still being written
    epochs = [int(p.stem) for p in clipper.select_segments(tmp_path, start=1014, end=1030)]
    assert epochs == [1012]
