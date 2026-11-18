"""Where secrets come from: client ids/secrets from the environment, OAuth tokens from a 0600 file."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .publishers.base import AuthError


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise AuthError(f"environment variable {name} is not set")
    return value


class TokenStore:
    """One JSON file per platform. Written atomically, readable by the owner only."""

    def __init__(self, directory: Path):
        self.dir = directory

    def _path(self, platform: str) -> Path:
        return self.dir / f"{platform}.json"

    def load(self, platform: str) -> dict | None:
        try:
            return json.loads(self._path(platform).read_text())
        except FileNotFoundError:
            return None

    def require(self, platform: str) -> dict:
        tokens = self.load(platform)
        if tokens is None:
            raise AuthError(f"no stored tokens for {platform}; authorize it first")
        return tokens

    def save(self, platform: str, tokens: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        tmp = self._path(platform).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tokens, f)
        os.replace(tmp, self._path(platform))
