"""Per-platform daily upload counter, persisted so a restart does not reset the budget."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path


class DailyQuota:
    def __init__(
        self,
        path: Path,
        limits: Mapping[str, int],
        today: Callable[[], date] = date.today,
    ):
        self.path, self.limits, self._today = path, dict(limits), today

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def used(self, platform: str) -> int:
        return self._load().get(platform, {}).get(self._today().isoformat(), 0)

    def allowed(self, platform: str) -> bool:
        limit = self.limits.get(platform)
        return limit is None or self.used(platform) < limit

    def record(self, platform: str) -> None:
        data, day = self._load(), self._today().isoformat()
        # keep only today: old days are never read
        data[platform] = {day: data.get(platform, {}).get(day, 0) + 1}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.path)
