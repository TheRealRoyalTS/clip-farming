from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ClipConfig:
    pre_seconds: float = 15.0
    post_seconds: float = 20.0
    buffer_seconds: float = 120.0


@dataclass(frozen=True)
class RetentionConfig:
    rejected_hours: float = 24.0  # video of a rejected clip
    published_days: float = 7.0  # video once every platform has its copy
    pending_days: float = 3.0  # undecided clips lose value fast
    min_free_gb: float = 2.0  # below this no new clip is cut
    interval_minutes: float = 60.0


@dataclass(frozen=True)
class StreamConfig:
    # streamlink quality list: first available wins. 'best' alone can mean 1080p60.
    quality: str = "720p60,720p,best"


@dataclass(frozen=True)
class DetectorConfig:
    window_seconds: float = 5.0
    baseline_seconds: float = 120.0
    sigma: float = 3.0
    min_ratio: float = 3.0
    min_messages: float = 8.0
    cooldown_seconds: float = 45.0
    min_signals: int = 2  # 'rate' is one of them; 1 = chat rate alone is enough
    min_unique: int = 8  # distinct chatters in the window
    min_unique_ratio: float = 0.5  # distinct chatters / messages: below this it is spam
    min_same: int = 5  # messages equal to the most common one ...
    min_same_users: int = 4  # ... sent by at least this many different chatters
    audio_jump_db: float = 10.0  # loudness above the previous minute; normal talk varies ~12 dB
    hype_weight: float = 2.0
    hype_terms: tuple[str, ...] = ("KEKW", "LUL", "LULW", "OMEGALUL", "POGGERS", "Pog", "CLIP")


@dataclass(frozen=True)
class PublishConfig:
    platforms: tuple[str, ...] = ("dryrun",)
    daily_limits: dict[str, int] = field(default_factory=dict)  # platform -> uploads per day
    attempts: int = 3
    base_delay: float = 2.0
    title_template: str = "{channel} clip #shorts"
    description_template: str = "Clip from twitch.tv/{channel}"


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    clip: ClipConfig = field(default_factory=ClipConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    channels: tuple[str, ...] = ()


_TWITCH = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{1,25})/?(?:[?#].*)?$")


def channel_from(value: str) -> str:
    """Accept 'name', '#name' or a twitch.tv link; return the lowercase channel name."""
    value = value.strip()
    if m := _TWITCH.match(value):
        return m.group(1).lower()
    if re.fullmatch(r"#?[A-Za-z0-9_]{1,25}", value):
        return value.lstrip("#").lower()
    raise ValueError(f"not a Twitch channel name or link: {value!r}")


def load(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    det = raw.get("detector", {})
    if "hype_terms" in det:
        det["hype_terms"] = tuple(det["hype_terms"])
    pub = raw.get("publish", {})
    if "platforms" in pub:
        pub["platforms"] = tuple(pub["platforms"])
    return Config(
        data_dir=Path(raw.get("data_dir", "data")),
        clip=ClipConfig(**raw.get("clip", {})),
        detector=DetectorConfig(**det),
        publish=PublishConfig(**pub),
        stream=StreamConfig(**raw.get("stream", {})),
        retention=RetentionConfig(**raw.get("retention", {})),
        channels=tuple(channel_from(c.get("url") or c["name"]) for c in raw.get("channels", [])),
    )
