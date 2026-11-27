"""Bounded public HTTP collection with sanitized source diagnostics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import Request, urlopen

MAX_BODY_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT = 10
XUEQIU_UNAUTHORIZED = "XUEQIU_COOKIE_EXPIRED_OR_UNAUTHORIZED"


@dataclass(frozen=True)
class Response:
    source: str
    reason: str | None = None
    body: bytes | None = None
    fetched_at: datetime | None = None
    http_status: int | None = None


@dataclass(frozen=True)
class Feed:
    platform: str
    source: str
    url: str
    validate: Callable[[bytes], str | None]
    headers: tuple[tuple[str, str], ...] = ()
    xueqiu: bool = False

    def collect(self, now: Callable[[], datetime]) -> Response:
        if not self.url:
            return Response(self.source, "SOURCE_NOT_CONFIGURED")
        try:
            url = urlsplit(self.url)
            if url.scheme not in {"https", "http"} or not url.hostname or url.username is not None:
                raise ValueError
            request = Request(self.url, headers={"User-Agent": "emotion-index/0.1"})
            for header, value in self.headers:
                if "\r" in value or "\n" in value:
                    raise ValueError
                request.add_unredirected_header(header, value)
        except ValueError:
            return Response(self.source, "INVALID_SOURCE_CONFIGURATION")

        try:
            try:
                stream = urlopen(request, timeout=HTTP_TIMEOUT)
            except HTTPError as error:
                stream = error
            with stream:
                status = stream.code
                body = stream.read(MAX_BODY_BYTES + 1)
        except (TimeoutError, URLError, HTTPException, OSError, ValueError) as error:
            cause = error.reason if isinstance(error, URLError) else error
            reason = "TIMEOUT" if isinstance(cause, TimeoutError) else "NETWORK_ERROR"
            return Response(self.source, reason)

        fetched_at = now()
        if len(body) > MAX_BODY_BYTES:
            return Response(self.source, "RESPONSE_TOO_LARGE", fetched_at=fetched_at, http_status=status)
        reason = self._failure(status, body)
        if self._reflects_credentials(url.query, body):
            return Response(self.source, reason or "CREDENTIALS_IN_RESPONSE",
                            fetched_at=fetched_at, http_status=status)
        return Response(self.source, reason, body, fetched_at, status)

    def _failure(self, status: int, body: bytes) -> str | None:
        if status == 429:
            return "RATE_LIMITED"
        if self.xueqiu and status in {401, 403}:
            return XUEQIU_UNAUTHORIZED
        if 200 <= status < 300:
            try:
                reason = self.validate(body)
            except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
                reason = "INVALID_RESPONSE"
        else:
            reason = f"HTTP_{status}"
        if self.xueqiu and reason:
            lower = body.lower()
            markers = (b"400016", b"cookie expired", b"invalid cookie", b"logging back into your account",
                       "Cookie 已失效".lower().encode(), "缺少雪球用户登录后的 Cookie".lower().encode())
            if any(marker in lower for marker in markers):
                return XUEQIU_UNAUTHORIZED
        return reason

    def _reflects_credentials(self, query: str, body: bytes) -> bool:
        secrets = [value for key, value in parse_qsl(query)
                   if key.casefold() in {"key", "token", "access_key", "api_key", "apikey", "access_token", "code"}]
        for header, value in self.headers:
            if header.casefold() == "cookie":
                secrets.extend(part.partition("=")[2].strip() for part in value.split(";"))
            else:
                secrets.append(value)
        return any(
            len(secret) >= 8 and (secret.encode() in body or quote(secret, safe="").encode() in body)
            for secret in secrets
        )


def json_list(field: str) -> Callable[[bytes], None]:
    def validate(body: bytes) -> None:
        if not isinstance(json.loads(body)[field], list):
            raise ValueError(f"{field} must be a list")
    return validate
