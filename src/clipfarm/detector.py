"""Viral-moment detector. Pure logic: callers pass timestamps, so it is testable without a clock.

A chat-rate spike is the gate. It becomes a trigger only when enough *independent* signals agree:

  rate    weighted messages in the window far above this channel's own baseline (the gate)
  unique  many different chatters reacting, not one person spamming
  same    many different chatters sending the same message (emote / "NO WAY" chains)
  audio   the stream's loudness jumped (streamer shouts or laughs), supplied by `audio_check`
"""

from __future__ import annotations

import math
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass

from .config import DetectorConfig


@dataclass(frozen=True)
class Trigger:
    channel: str
    at: float  # epoch seconds when the spike was detected
    rate: float  # weighted messages in the window
    baseline_mean: float
    baseline_std: float
    signals: tuple[str, ...] = ("rate",)
    unique: int = 0  # distinct chatters in the window
    same: int = 0  # messages in the window equal to the most common one


@dataclass(frozen=True)
class _Event:
    ts: float
    weight: float
    user: str
    text: str  # normalised


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


class SpikeDetector:
    def __init__(
        self,
        channel: str,
        cfg: DetectorConfig,
        audio_check: Callable[[float], bool] | None = None,
    ):
        self.channel = channel
        self.cfg = cfg
        self.audio_check = audio_check
        self.rejected = 0  # chat spikes that lacked a second signal, counted once per window
        self._events: deque[_Event] = deque()
        self._last_trigger = -math.inf
        self._last_reject = -math.inf
        self._first_ts: float | None = None
        self._hype = {t.lower() for t in cfg.hype_terms}

    def weight(self, text: str) -> float:
        words = {w.strip(".,!?").lower() for w in text.split()}
        return self.cfg.hype_weight if words & self._hype else 1.0

    def feed(self, ts: float, text: str, user: str = "") -> Trigger | None:
        if self._first_ts is None:
            self._first_ts = ts
        self._events.append(_Event(ts, self.weight(text), user, normalise(text)))
        horizon = ts - self.cfg.baseline_seconds - self.cfg.window_seconds
        while self._events and self._events[0].ts < horizon:
            self._events.popleft()
        return self._evaluate(ts)

    def _evaluate(self, now: float) -> Trigger | None:
        cfg = self.cfg
        if self._first_ts is None or now - self._first_ts < cfg.baseline_seconds:
            return None  # warm-up: no baseline yet, so every start-of-stream burst would look viral
        if now - self._last_trigger < cfg.cooldown_seconds:
            return None
        win_start = now - cfg.window_seconds
        window = [e for e in self._events if e.ts > win_start]
        current = sum(e.weight for e in window)
        if current < cfg.min_messages:
            return None

        # Baseline: tile the history before the current window into window-sized buckets.
        n_buckets = int(cfg.baseline_seconds // cfg.window_seconds)
        buckets = [0.0] * n_buckets
        for e in self._events:
            if e.ts > win_start:
                continue
            idx = int((win_start - e.ts) // cfg.window_seconds)
            if idx < n_buckets:
                buckets[idx] += e.weight
        mean = sum(buckets) / n_buckets
        std = math.sqrt(sum((b - mean) ** 2 for b in buckets) / n_buckets)

        if not (current > mean + cfg.sigma * std and current >= cfg.min_ratio * max(mean, 1.0)):
            return None

        signals = ["rate"]
        users = {e.user for e in window}
        if len(users) >= cfg.min_unique and len(users) / len(window) >= cfg.min_unique_ratio:
            signals.append("unique")
        top_text, same = Counter(e.text for e in window).most_common(1)[0]
        same_users = {e.user for e in window if e.text == top_text}
        if same >= cfg.min_same and len(same_users) >= cfg.min_same_users:
            signals.append("same")
        if self.audio_check is not None and self.audio_check(now):
            signals.append("audio")

        if len(signals) < cfg.min_signals:
            if now - self._last_reject >= cfg.window_seconds:
                self.rejected += 1
                self._last_reject = now
            return None  # no cooldown: a second signal may still arrive within this burst
        self._last_trigger = now
        return Trigger(self.channel, now, current, mean, std, tuple(signals), len(users), same)
