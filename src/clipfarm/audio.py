"""Loudness signal: the streamer shouting or laughing shows up as a jump in segment volume."""

from __future__ import annotations

import re
import statistics
import subprocess
from collections.abc import Callable
from pathlib import Path

from .recorder import segment_epoch

_MEAN = re.compile(r"mean_volume:\s*(-?inf|-?[\d.]+)\s*dB")
SILENCE_DB = -91.0


def measure(path: Path) -> float | None:
    """Mean volume of a segment in dB (volumedetect), None when ffmpeg cannot read it."""
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        check=False, capture_output=True, text=True,
    )  # fmt: skip
    m = _MEAN.search(r.stderr)
    if r.returncode != 0 or not m:
        return None
    return SILENCE_DB if "inf" in m.group(1) else float(m.group(1))


def is_loud_spike(levels: list[float], jump_db: float, recent: int = 3, base: int = 10) -> bool:
    """True when the loudest of the `recent` newest segments is `jump_db` above the median of the
    `base` segments before them. Needs a full baseline, so it stays False while the buffer fills."""
    if len(levels) < recent + base:
        return False
    baseline = statistics.median(levels[-(recent + base) : -recent])
    return max(levels[-recent:]) >= baseline + jump_db


class AudioTracker:
    """Measures every completed segment once. The newest file is skipped: it is still being written."""

    def __init__(self, buffer_dir: Path, measure_fn: Callable[[Path], float | None] = measure):
        self.buffer_dir = buffer_dir
        self._measure = measure_fn
        self._levels: dict[int, float] = {}

    def update(self) -> None:
        segs = sorted(self.buffer_dir.glob("*.ts"), key=segment_epoch)[:-1]
        live = {segment_epoch(p) for p in segs}
        for p in segs:
            e = segment_epoch(p)
            if e not in self._levels and (db := self._measure(p)) is not None:
                self._levels[e] = db
        for e in [e for e in self._levels if e not in live]:
            del self._levels[e]  # pruned from disk

    def levels(self) -> list[float]:
        return [self._levels[e] for e in sorted(self._levels)]

    def spike(self, jump_db: float) -> bool:
        return is_loud_spike(self.levels(), jump_db)
