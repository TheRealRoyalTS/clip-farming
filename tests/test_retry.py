import pytest

from clipfarm.publishers.base import AuthError, PermanentError, RateLimited, TransientError
from clipfarm.retry import call_with_retry


def flaky(failures, exc=TransientError):
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] <= failures:
            raise exc("boom")
        return "ok"

    return fn, state


def test_retries_transient_then_succeeds_with_exponential_backoff():
    fn, state = flaky(2)
    sleeps = []
    assert call_with_retry(fn, attempts=3, base_delay=2.0, sleep=sleeps.append) == "ok"
    assert state["n"] == 3 and sleeps == [2.0, 4.0]


def test_gives_up_after_attempts_and_raises_last_transient():
    fn, state = flaky(10)
    with pytest.raises(TransientError):
        call_with_retry(fn, attempts=3, sleep=lambda s: None)
    assert state["n"] == 3


@pytest.mark.parametrize("exc", [RateLimited, AuthError, PermanentError])
def test_non_transient_errors_are_not_retried(exc):
    fn, state = flaky(10, exc)
    with pytest.raises(exc):
        call_with_retry(fn, attempts=5, sleep=lambda s: None)
    assert state["n"] == 1
