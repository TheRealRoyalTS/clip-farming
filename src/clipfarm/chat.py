"""Anonymous Twitch chat reader over IRC (read-only, no auth)."""

from __future__ import annotations

import asyncio
import ssl
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

HOST, PORT = "irc.chat.twitch.tv", 6697


@dataclass(frozen=True)
class ChatMessage:
    channel: str
    user: str
    text: str
    ts: float


def parse_line(line: str, now: float) -> ChatMessage | None:
    """Parse ':user!user@user.tmi.twitch.tv PRIVMSG #chan :text'. Return None for anything else."""
    if " PRIVMSG " not in line:
        return None
    if line.startswith("@"):  # strip IRCv3 tags
        _, _, line = line.partition(" ")
    prefix, _, rest = line.partition(" PRIVMSG ")
    chan, _, text = rest.partition(" :")
    if not prefix.startswith(":") or not chan.startswith("#"):
        return None
    user = prefix[1:].split("!", 1)[0]
    return ChatMessage(chan[1:].lower(), user, text, now)


async def read_chat(channel: str) -> AsyncIterator[ChatMessage]:
    """Yield messages forever; reconnect with backoff on connection loss."""
    backoff = 1.0
    while True:
        try:
            reader, writer = await asyncio.open_connection(
                HOST, PORT, ssl=ssl.create_default_context()
            )
            writer.write(
                f"NICK justinfan{int(time.time()) % 90000 + 10000}\r\nJOIN #{channel}\r\n".encode()
            )
            await writer.drain()
            backoff = 1.0
            while raw := await reader.readline():
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue
                if msg := parse_line(line, time.time()):
                    yield msg
        except (OSError, ssl.SSLError):
            pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)
