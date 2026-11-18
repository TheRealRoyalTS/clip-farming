"""YouTube adapter against a fake API service. No network and no credentials are used."""

import json
import os
import stat

import pytest

pytest.importorskip("googleapiclient")
import httplib2
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from clipfarm.config import YouTubeConfig, load
from clipfarm.credentials import TokenStore
from clipfarm.pipeline import publish_approved, read_state
from clipfarm.publishers import PublishContext, build
from clipfarm.publishers.base import (
    AuthError,
    PermanentError,
    PublishRequest,
    RateLimited,
    TransientError,
)
from clipfarm.publishers.youtube import YouTubePublisher, authorize, classify
from clipfarm.queue import Queue
from clipfarm.quota import DailyQuota


class FakeRequest:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.chunks = 0

    def next_chunk(self, num_retries=0):
        assert num_retries == 0, "retries belong to the pipeline"
        self.chunks += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeService:
    def __init__(self, *outcomes):
        self.request = FakeRequest(outcomes)
        self.inserts = []

    def videos(self):
        return self

    def insert(self, **kwargs):
        self.inserts.append(kwargs)
        return self.request


def http_error(status, reason="", message="boom"):
    body = {"error": {"message": message, "errors": [{"reason": reason}]}}
    return HttpError(httplib2.Response({"status": str(status)}), json.dumps(body).encode())


def pub(service, cfg=None, tmp_path=None):
    return YouTubePublisher(
        TokenStore(tmp_path or "unused"),
        cfg or YouTubeConfig(),
        service_factory=lambda: service,
        media_factory=lambda p: f"media:{p.name}",
    )


REQ = PublishRequest(video=__import__("pathlib").Path("/x/clip.mp4"), title="T", description="D")


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (401, "", AuthError),
        (403, "quotaExceeded", RateLimited),
        (403, "uploadLimitExceeded", RateLimited),
        (403, "rateLimitExceeded", RateLimited),
        (429, "", RateLimited),
        (403, "forbidden", AuthError),
        (500, "", TransientError),
        (503, "backendError", TransientError),
        (400, "invalidVideo", PermanentError),
        (404, "", PermanentError),
    ],
)
def test_classify(status, reason, expected):
    assert type(classify(status, reason, "m")) is expected


def test_successful_upload_over_two_chunks_returns_id_and_shorts_url():
    svc = FakeService((None, None), (None, {"id": "abc123"}))
    res = pub(svc).publish(REQ)
    assert (res.remote_id, res.url) == ("abc123", "https://youtube.com/shorts/abc123")
    assert svc.request.chunks == 2
    assert svc.inserts[0]["media_body"] == "media:clip.mp4"
    assert svc.inserts[0]["part"] == "snippet,status"


def test_new_uploads_are_private_unless_configured_otherwise():
    # Guarantee: nothing goes public by default.
    assert YouTubeConfig().privacy == "private"
    svc = FakeService((None, {"id": "a"}))
    pub(svc).publish(REQ)
    assert svc.inserts[0]["body"]["status"]["privacyStatus"] == "private"


def test_body_carries_title_description_category_and_kids_flag():
    svc = FakeService((None, {"id": "a"}))
    cfg = YouTubeConfig(privacy="unlisted", category_id="22", made_for_kids=False)
    pub(svc, cfg).publish(REQ)
    body = svc.inserts[0]["body"]
    assert body["snippet"] == {"title": "T", "description": "D", "tags": [], "categoryId": "22"}
    assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [(403, "quotaExceeded", RateLimited), (401, "", AuthError), (500, "", TransientError),
     (400, "badRequest", PermanentError)],
)  # fmt: skip
def test_api_errors_during_upload_map_to_pipeline_errors(status, reason, expected):
    svc = FakeService((None, None), http_error(status, reason))
    with pytest.raises(expected, match=str(status)):
        pub(svc).publish(REQ)


def test_connection_errors_are_transient():
    with pytest.raises(TransientError):
        pub(FakeService(ConnectionResetError("reset"))).publish(REQ)


def test_expired_refresh_token_asks_for_a_new_login():
    def broken():
        raise RefreshError("invalid_grant")

    p = YouTubePublisher(TokenStore("x"), YouTubeConfig(), service_factory=broken)
    with pytest.raises(AuthError, match="clipfarm auth youtube"):
        p.publish(REQ)


def test_missing_stored_tokens_is_an_auth_error_not_a_crash(tmp_path):
    p = YouTubePublisher(TokenStore(tmp_path), YouTubeConfig())
    with pytest.raises(AuthError, match="authorize"):
        p.publish(REQ)


def test_response_without_video_id_is_permanent():
    with pytest.raises(PermanentError, match="without a video id"):
        pub(FakeService((None, {}))).publish(REQ)


# ---- through the real pipeline ----


def approved(q, tmp_path, *ids):
    for cid in ids:
        f = tmp_path / f"{cid}.mp4"
        f.write_bytes(b"v")
        q.add(cid, f, {"channel": "somechan", "at": 1.0, "rate": 9.0})
        q.approve(cid)


def test_pipeline_publishes_and_records_the_youtube_url(tmp_path):
    q = Queue(tmp_path / "q")
    approved(q, tmp_path, "a")
    svc = FakeService((None, {"id": "vid1"}))
    publish_approved(q, [pub(svc)], DailyQuota(tmp_path / "quota.json", {}), sleep=lambda s: None)
    assert q.list("published") == ["a"]
    assert (
        read_state(q.root / "published" / "a")["youtube"]["url"]
        == "https://youtube.com/shorts/vid1"
    )
    assert "somechan" in svc.inserts[0]["body"]["snippet"]["title"]


def test_quota_exceeded_from_youtube_stops_further_uploads_this_run(tmp_path):
    q = Queue(tmp_path / "q")
    approved(q, tmp_path, "a", "b")
    svc = FakeService(http_error(403, "quotaExceeded"))
    rep = publish_approved(
        q, [pub(svc)], DailyQuota(tmp_path / "quota.json", {}), sleep=lambda s: None
    )
    assert len(svc.inserts) == 1, "no second upload after the quota error"
    assert q.list("approved") == ["a", "b"] and rep.failed == []


# ---- login ----


class FakeCreds:
    def __init__(self, info):
        self.info = info

    def to_json(self):
        return json.dumps(self.info)


GOOD_INFO = {"refresh_token": "r", "client_id": "i", "client_secret": "s", "token": "t"}


class FakeFlow:
    def __init__(self, info):
        self.info = info
        self.kwargs = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return FakeCreds(self.info)


def test_authorize_stores_token_owner_only_and_asks_for_offline_access(tmp_path, monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = FakeFlow(GOOD_INFO)
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda p, scopes: flow)
    store = TokenStore(tmp_path / "secrets")
    authorize(tmp_path / "client_secret.json", store)
    assert store.require("youtube")["refresh_token"] == "r"
    assert stat.S_IMODE(os.stat(tmp_path / "secrets" / "youtube.json").st_mode) == 0o600
    assert flow.kwargs["access_type"] == "offline" and flow.kwargs["prompt"] == "consent"


def test_authorize_without_refresh_token_stores_nothing(tmp_path, monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = FakeFlow({"token": "t"})
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda p, scopes: flow)
    store = TokenStore(tmp_path / "secrets")
    with pytest.raises(AuthError, match="refresh token"):
        authorize(tmp_path / "c.json", store)
    assert store.load("youtube") is None


# ---- wiring ----


def test_registry_builds_youtube_only_with_a_context(tmp_path):
    with pytest.raises(ValueError, match="PublishContext"):
        build(["youtube"])
    ctx = PublishContext(TokenStore(tmp_path), YouTubeConfig())
    assert [p.name for p in build(["dryrun", "youtube"], ctx)] == ["dryrun", "youtube"]


def test_youtube_config_defaults_and_overrides(tmp_path):
    assert (
        load(__import__("pathlib").Path("config.example.toml")).publish.youtube.privacy == "private"
    )
    f = tmp_path / "c.toml"
    f.write_text('[publish.youtube]\nprivacy = "unlisted"\n')
    assert load(f).publish.youtube.privacy == "unlisted"
    assert load(f).publish.youtube.category_id == "20"
