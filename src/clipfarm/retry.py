from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from .publishers.base import TransientError

T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry only TransientError with exponential backoff. Every other error propagates at once:
    RateLimited, AuthError and PermanentError are not helped by repeating the call."""
    for i in range(attempts):
        try:
            return fn()
        except TransientError:
            if i == attempts - 1:
                raise
            sleep(base_delay * 2**i)
    raise AssertionError("attempts must be >= 1")
