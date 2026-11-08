"""SMTP delivery mechanics; the reacting module owns recipients and opt-in policy."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage


def _mailbox(value: str) -> str:
    address = Address(addr_spec=value)
    if not address.username or not address.domain:
        raise ValueError("SMTP requires a complete mailbox address")
    return address.addr_spec


async def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    template_id: str | None = None,
    template_data: dict | None = None,
) -> bool:
    """Return True only after the configured SMTP server accepts the message.

    This is server acceptance, not proof of inbox delivery. SMTP takes the
    rendered subject/body; provider-hosted templates are not supported.
    Configuration and credentials come only from the server environment.
    Plaintext is restricted to a credential-free loopback development sink.
    """
    if template_id is not None or template_data is not None:
        raise ValueError("SMTP email requires a rendered subject and body")
    host = os.environ.get("SMTP_HOST", "").strip()
    sender = os.environ.get("SMTP_FROM", "").strip()
    if not host or not sender:
        raise ValueError("Configure SMTP_HOST and SMTP_FROM before sending email")
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
    if security not in {"starttls", "ssl", "none"}:
        raise ValueError("SMTP_SECURITY must be starttls, ssl, or none")
    port = int(os.environ.get("SMTP_PORT", "465" if security == "ssl" else "587"))
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")
    username = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        raise ValueError("Configure SMTP_USER and SMTP_PASSWORD together")
    if security == "none":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback or username or password:
            raise ValueError("Plaintext SMTP is only allowed for a credential-free loopback sink")

    sender = _mailbox(sender)
    recipient = _mailbox(to)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    def deliver() -> bool:
        options = {"timeout": 15}
        if security == "ssl":
            options["context"] = ssl.create_default_context()
        client = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
        with client(host, port, **options) as smtp:
            if security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password)
            return not smtp.send_message(message, from_addr=sender, to_addrs=[recipient])

    return await asyncio.to_thread(deliver)
