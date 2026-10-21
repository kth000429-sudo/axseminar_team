"""SMTP delivery adapter for the marketing application dashboard."""

from __future__ import annotations

import os
import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage


def _config_value(config: dict[str, str] | None, key: str, default: str = "") -> str:
    if config is not None and key in config:
        return str(config[key])
    return os.getenv(key, default)


def smtp_is_configured(config: dict[str, str] | None = None) -> bool:
    required = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
    values = {name: _config_value(config, name).strip() for name in required}
    if not all(values.values()):
        return False
    if any("example." in values[name].lower() or values[name].lower().startswith("your_") for name in required):
        return False
    try:
        port = int(_config_value(config, "SMTP_PORT", "587"))
    except ValueError:
        return False
    return 1 <= port <= 65535


def send_email(
    to_address: str,
    subject: str,
    body: str,
    config: dict[str, str] | None = None,
    attachments: Sequence[tuple[str, bytes, str]] = (),
) -> None:
    """Deliver one message over implicit TLS (465) or STARTTLS (other ports)."""
    if not smtp_is_configured(config):
        raise RuntimeError(
            "SMTP 설정이 필요합니다. 로컬 .env 또는 Streamlit Cloud Secrets에 SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD, SMTP_FROM을 확인하세요."
        )

    host = _config_value(config, "SMTP_HOST").strip()
    port = int(_config_value(config, "SMTP_PORT", "587"))
    username = _config_value(config, "SMTP_USER").strip()
    password = _config_value(config, "SMTP_PASSWORD")
    sender = _config_value(config, "SMTP_FROM").strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_address
    message.set_content(body)
    for filename, content, content_type in attachments:
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    tls_context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=tls_context) as client:
            client.login(username, password)
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=tls_context)
            client.ehlo()
            client.login(username, password)
            client.send_message(message)
