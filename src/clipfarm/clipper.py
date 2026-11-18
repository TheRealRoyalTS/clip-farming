"""Cut [at - pre, at + post] out of the rolling buffer and reformat to 9:16."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .recorder import SEGMENT_SECONDS, segment_epoch

# Length assumed for the newest segment, whose end is not known yet. With `-c copy` segments only
# end at keyframes, and measured gaps on Twitch are 6 s.
NEWEST_SEGMENT_SECONDS = SEGMENT_SECONDS

# The blurred background is computed at 1/4 size and scaled up: blurring 1080x1920 directly made
# a 12 s clip take 48.5 s to encode, this takes 9.8 s (8 cores, measured).
VERTICAL_FILTER = (
    "[0:v]split[a][b];"
    "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=4:2,"
    "scale=1080:1920[bg];"
    "[b]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
)


def select_segments(buffer_dir: Path, start: float, end: float) -> list[Path]:
    """Segments overlapping [start, end], oldest first.

    A segment covers [its epoch, the next segment's epoch): the real length is the gap to its
    successor, never a constant, because `-c copy` cuts at keyframes, not at -segment_time."""
    segs = sorted(buffer_dir.glob("*.ts"), key=segment_epoch)
    chosen = []
    for i, p in enumerate(segs):
        begin = segment_epoch(p)
        finish = segment_epoch(segs[i + 1]) if i + 1 < len(segs) else begin + NEWEST_SEGMENT_SECONDS
        if finish > start and begin < end:
            chosen.append(p)
    return chosen


def concat_list(segments: list[Path]) -> str:
    return "".join(f"file '{p.resolve()}'\n" for p in segments)


def ffmpeg_args(
    list_file: Path, first_epoch: int, start: float, duration: float, out: Path
) -> list[str]:
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-ss", f"{max(start - first_epoch, 0):.2f}", "-t", f"{duration:.2f}",
        "-filter_complex", VERTICAL_FILTER,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out),
    ]  # fmt: skip


class NoFootage(Exception):
    pass


def cut(buffer_dir: Path, at: float, pre: float, post: float, out: Path) -> Path:
    start, end = at - pre, at + post
    segments = select_segments(buffer_dir, start, end)
    if not segments:
        raise NoFootage(f"no buffered segments for {start:.0f}..{end:.0f} in {buffer_dir}")
    out.parent.mkdir(parents=True, exist_ok=True)
    list_file = out.with_suffix(".txt")
    list_file.write_text(concat_list(segments))
    try:
        subprocess.run(
            ffmpeg_args(list_file, segment_epoch(segments[0]), start, end - start, out),
            check=True, capture_output=True,
        )  # fmt: skip
    finally:
        list_file.unlink(missing_ok=True)
    return out
