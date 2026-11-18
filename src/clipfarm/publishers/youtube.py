"""YouTube Shorts upload through the YouTube Data API v3 (resumable upload).

Needs the `youtube` extra: pip install 'clipfarm[youtube]'. Run `clipfarm auth youtube` once to
store a refresh token. YouTube classifies a vertical or square video of up to three minutes as a
Short by itself, so there is no Shorts flag to set.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import YouTubeConfig
from .base import (
    AuthError,
    PermanentError,
    PublishError,
    PublishRequest,
    PublishResult,
    RateLimited,
    TransientError,
)

if TYPE_CHECKING:  # imported for the annotation only: credentials imports this package
    from ..credentials import TokenStore

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHUNK_BYTES = 8 * 1024 * 1024
# Error reasons that mean "stop uploading for now". quotaExceeded and uploadLimitExceeded are the
# ones YouTube documents for uploads; the others are the generic Google rate-limit reasons.
RATE_REASONS = {
    "quotaExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "dailyLimitExceeded",
    "uploadLimitExceeded",
}


def classify(status: int, reason: str, message: str) -> PublishError:
    """Map an HTTP error from the API to the error type the pipeline reacts to."""
    text = f"HTTP {status} {reason}: {message}".strip()
    if status == 401:
        return AuthError(text)
    if status == 429 or (status == 403 and reason in RATE_REASONS):
        return RateLimited(text)
    if status == 403:  # wrong scope, no channel, account suspended: a human has to look
        return AuthError(text)
    if status >= 500:
        return TransientError(text)
    return PermanentError(text)


def _http_error_parts(err: Any) -> tuple[int, str, str]:
    status = int(getattr(err.resp, "status", 0) or 0)
    reason, message = "", ""
    try:
        detail = json.loads(err.content.decode("utf-8", "replace"))["error"]
        message = str(detail.get("message", ""))
        reason = str((detail.get("errors") or [{}])[0].get("reason", ""))
    except (ValueError, KeyError, AttributeError, TypeError):
        pass
    return status, reason, message


def _import_google() -> tuple[Any, Any, Any, Any]:
    try:
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as e:  # blocks the platform for the run, like missing credentials
        raise AuthError("YouTube support is not installed: pip install 'clipfarm[youtube]'") from e
    return RefreshError, Credentials, build, HttpError


class YouTubePublisher:
    name = "youtube"

    def __init__(
        self,
        tokens: TokenStore,
        cfg: YouTubeConfig,
        service_factory: Callable[[], Any] | None = None,
        media_factory: Callable[[Path], Any] | None = None,
    ):
        self.tokens, self.cfg = tokens, cfg
        self._service_factory = service_factory
        self._media_factory = media_factory

    def _service(self) -> Any:
        if self._service_factory:
            return self._service_factory()
        _, credentials, build, _ = _import_google()
        creds = credentials.from_authorized_user_info(self.tokens.require("youtube"), SCOPES)
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def _media(self, video: Path) -> Any:
        if self._media_factory:
            return self._media_factory(video)
        _import_google()
        from googleapiclient.http import MediaFileUpload

        return MediaFileUpload(
            str(video), mimetype="video/mp4", chunksize=CHUNK_BYTES, resumable=True
        )

    def body(self, req: PublishRequest) -> dict:
        return {
            "snippet": {
                "title": req.title,
                "description": req.description,
                "tags": list(req.tags),
                "categoryId": self.cfg.category_id,
            },
            "status": {
                "privacyStatus": self.cfg.privacy,
                "selfDeclaredMadeForKids": self.cfg.made_for_kids,
            },
        }

    def publish(self, req: PublishRequest) -> PublishResult:
        refresh_error, _, _, http_error = _import_google()
        try:
            service = self._service()
            request = service.videos().insert(
                part="snippet,status", body=self.body(req), media_body=self._media(req.video)
            )
            response = None
            while response is None:
                _, response = request.next_chunk(num_retries=0)  # retries are the pipeline's job
        except http_error as e:
            raise classify(*_http_error_parts(e)) from e
        except refresh_error as e:
            raise AuthError(f"token refresh failed, run `clipfarm auth youtube` again: {e}") from e
        except PublishError:
            raise
        except OSError as e:  # connection reset, timeout, TLS
            raise TransientError(f"network error during upload: {e}") from e
        video_id = response.get("id")
        if not video_id:
            raise PermanentError(f"upload finished without a video id: {response!r}")
        return PublishResult(remote_id=video_id, url=f"https://youtube.com/shorts/{video_id}")


def authorize(client_secrets: Path, tokens: TokenStore) -> None:
    """One-time browser login. Stores the refresh token (never printed) in the token store."""
    _import_google()
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    # offline + consent make Google return a refresh token even if this account authorised before
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    info = json.loads(creds.to_json())
    if not info.get("refresh_token"):
        raise AuthError("Google returned no refresh token; revoke the app's access and retry")
    tokens.save("youtube", info)
