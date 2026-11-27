"""Optional SMTP report delivery and Bark alerts configured by the environment."""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from http.client import HTTPException
from typing import Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from emotion_index.domain.market import DivergenceFlag
from emotion_index.integrations.providers.http import XUEQIU_UNAUTHORIZED

BARK_FLAGS = frozenset({
    DivergenceFlag.EXTREME_EUPHORIA_WARNING.value,
    DivergenceFlag.EXTREME_CAPITULATION_ALERT.value,
    "DEGRADED_LEXICON",
    XUEQIU_UNAUTHORIZED,
})
TIMEOUT = 15


def _send_email(config: Mapping[str, str], subject: str, body: str, html: str, filename: str) -> None:
    host = config["EMOTION_INDEX_SMTP_HOST"]
    mode = config.get("EMOTION_INDEX_SMTP_TLS", "starttls").lower()
    if mode not in {"starttls", "ssl"}:
        raise ValueError("invalid SMTP TLS mode")
    port = int(config.get("EMOTION_INDEX_SMTP_PORT", "465" if mode == "ssl" else "587"))
    recipients = [item.strip() for item in config["EMOTION_INDEX_SMTP_TO"].split(",") if item.strip()]
    sender = config["EMOTION_INDEX_SMTP_FROM"].strip()
    username = config.get("EMOTION_INDEX_SMTP_USER")
    password = config.get("EMOTION_INDEX_SMTP_PASSWORD")
    if not sender or not recipients or bool(username) != bool(password) or not 0 < port <= 65535:
        raise ValueError("invalid SMTP configuration")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(html, subtype="html", filename=filename)
    tls = ssl.create_default_context()
    connection = (smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=tls) if mode == "ssl"
                  else smtplib.SMTP(host, port, timeout=TIMEOUT))
    with connection as smtp:
        if mode == "starttls":
            smtp.starttls(context=tls)
        if username:
            smtp.login(username, password)
        if smtp.send_message(message, from_addr=sender, to_addrs=recipients):
            raise OSError("SMTP refused one or more recipients")


def _send_bark(config: Mapping[str, str], subject: str, flags: tuple[str, ...]) -> None:
    server = config.get("EMOTION_INDEX_BARK_URL", "https://api.day.app").rstrip("/")
    parsed = urlsplit(server)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.query or parsed.fragment):
        raise ValueError("Bark requires a credential-free HTTPS base URL")
    request = Request(server + "/push", data=json.dumps({
        "device_key": config["EMOTION_INDEX_BARK_KEY"], "title": subject,
        "body": "\n".join(flags), "group": "EmotionIndex",
    }, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"})
    with urlopen(request, timeout=TIMEOUT) as response:
        result = json.loads(response.read(65_536))
        if response.status != 200 or not isinstance(result, dict) or result.get("code") != 200:
            raise OSError("Bark rejected the push")


def notify_report(
    subject: str, body: str, html: str, filename: str, flags: tuple[str, ...],
    *, env: Mapping[str, str] | None = None,
) -> None:
    config = os.environ if env is None else env
    failures = []
    if config.get("EMOTION_INDEX_SMTP_HOST"):
        try:
            _send_email(config, subject, body, html, filename)
        except (OSError, ValueError, KeyError, HTTPException):
            failures.append("SMTP")
    alerts = tuple(sorted(BARK_FLAGS.intersection(flags)))
    if alerts and config.get("EMOTION_INDEX_BARK_KEY"):
        try:
            _send_bark(config, subject, alerts)
        except (OSError, ValueError, KeyError, HTTPException, RecursionError):
            failures.append("Bark")
    if failures:
        raise OSError("日报通知失败：" + "、".join(failures))
