import os
import stat

import pytest

from clipfarm.credentials import TokenStore, require_env
from clipfarm.publishers.base import AuthError


def test_require_env_returns_value(monkeypatch):
    monkeypatch.setenv("CF_TEST_SECRET", "s3")
    assert require_env("CF_TEST_SECRET") == "s3"


def test_require_env_missing_or_empty_is_auth_error(monkeypatch):
    monkeypatch.delenv("CF_TEST_SECRET", raising=False)
    with pytest.raises(AuthError, match="CF_TEST_SECRET"):
        require_env("CF_TEST_SECRET")
    monkeypatch.setenv("CF_TEST_SECRET", "")
    with pytest.raises(AuthError):
        require_env("CF_TEST_SECRET")


def test_token_roundtrip_and_overwrite(tmp_path):
    s = TokenStore(tmp_path / "secrets")
    s.save("youtube", {"refresh_token": "a"})
    s.save("youtube", {"refresh_token": "b"})
    assert s.load("youtube") == {"refresh_token": "b"}


def test_token_files_are_owner_only(tmp_path):
    s = TokenStore(tmp_path / "secrets")
    s.save("tiktok", {"t": 1})
    assert stat.S_IMODE(os.stat(tmp_path / "secrets" / "tiktok.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "secrets").st_mode) == 0o700


def test_missing_tokens(tmp_path):
    s = TokenStore(tmp_path)
    assert s.load("x") is None
    with pytest.raises(AuthError):
        s.require("x")
