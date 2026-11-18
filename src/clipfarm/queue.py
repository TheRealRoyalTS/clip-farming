"""Approval queue on the filesystem: data/queue/<state>/<id>/{clip.mp4,meta.json}."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

STATES = ("pending", "approved", "rejected", "published")


class Queue:
    def __init__(self, root: Path):
        self.root = root
        for s in STATES:
            (root / s).mkdir(parents=True, exist_ok=True)

    def add(self, clip_id: str, clip_file: Path, meta: dict) -> Path:
        d = self.root / "pending" / clip_id
        d.mkdir(parents=True)
        shutil.move(str(clip_file), d / "clip.mp4")
        (d / "meta.json").write_text(json.dumps(meta, indent=2))
        return d

    def list(self, state: str) -> list[str]:
        return sorted(p.name for p in (self.root / state).iterdir() if p.is_dir())

    def move(self, clip_id: str, src: str, dst: str) -> None:
        source = self.root / src / clip_id
        if not source.is_dir():
            raise KeyError(f"{clip_id} is not in {src}")
        dest = self.root / dst / clip_id
        source.rename(dest)
        if (dest / "meta.json").exists():
            os.utime(dest / "meta.json")  # age in the new state starts now

    def approve(self, clip_id: str) -> None:
        self.move(clip_id, "pending", "approved")

    def reject(self, clip_id: str) -> None:
        self.move(clip_id, "pending", "rejected")

    def meta(self, clip_id: str, state: str) -> dict:
        return json.loads((self.root / state / clip_id / "meta.json").read_text())
