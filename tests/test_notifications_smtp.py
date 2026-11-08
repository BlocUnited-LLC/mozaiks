"""Real loopback SMTP acceptance; reaction policy remains module-owned.

The reaction fixture uses explicit in-memory app records. It does not claim
to prove the notification_settings pack's separate persistence integration.
"""
from __future__ import annotations

import asyncio
import importlib.util
import smtplib
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mozaiksai.core.runtime.app.module_loader import ModuleReactionsManifest
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter


def test_smtp_adapter_uses_the_canonical_deterministic_pack_materializer():
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_templates_for_pack,
    )

    pack = Path(__file__).resolve().parents[1] / "factory_app/build_context/notifications"
    first = resolve_templates_for_pack(pack, "notifications", context_variables={})
    assert first == resolve_templates_for_pack(pack, "notifications", context_variables={})
    source = next(row["content"] for row in first if row["filename"] == "services/adapters/notifications/email.py")
    assert source == (pack / "templates/services/adapters/notifications/email.py").read_text(encoding="utf-8")
    compile(source, "services/adapters/notifications/email.py", "exec")


@pytest.fixture
def email_adapter(monkeypatch):
    for key in ("SMTP_HOST", "SMTP_FROM", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    path = Path(__file__).resolve().parents[1] / "factory_app/build_context/notifications/templates/services/adapters/notifications/email.py"
    spec = importlib.util.spec_from_file_location("notification_email_under_test", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


@pytest.fixture
async def smtp_sink(monkeypatch, email_adapter):
    messages = []

    async def receive(reader, writer):
        writer.write(b"220 localhost test sink\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                command = line.upper()
                if command.startswith((b"EHLO ", b"HELO ")):
                    writer.write(b"250 localhost\r\n")
                elif command.startswith(b"MAIL FROM:"):
                    writer.write(b"250 sender accepted\r\n")
                elif command.startswith(b"RCPT TO:"):
                    writer.write(b"550 rejected\r\n" if b"REJECTED@" in command else b"250 recipient accepted\r\n")
                elif command.startswith(b"DATA"):
                    writer.write(b"354 send content\r\n")
                    await writer.drain()
                    chunks = []
                    while content := await reader.readline():
                        if content == b".\r\n":
                            break
                        chunks.append(content[1:] if content.startswith(b"..") else content)
                    messages.append(BytesParser(policy=policy.default).parsebytes(b"".join(chunks)))
                    writer.write(b"250 message accepted\r\n")
                elif command.startswith(b"QUIT"):
                    writer.write(b"221 bye\r\n")
                    await writer.drain()
                    break
                elif command.startswith(b"RSET"):
                    writer.write(b"250 reset\r\n")
                else:
                    writer.write(b"502 command unsupported\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(server.sockets[0].getsockname()[1]))
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")
    monkeypatch.setenv("SMTP_SECURITY", "none")
    try:
        yield messages
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_real_smtp_acceptance_preserves_recipient_subject_and_body(email_adapter, smtp_sink):
    assert await email_adapter.send_email(to="recipient@example.test", subject="Ready ✓", body="Hello\n.leading dot\n") is True
    assert len(smtp_sink) == 1
    message = smtp_sink[0]
    assert str(message["From"]) == "sender@example.test"
    assert str(message["To"]) == "recipient@example.test"
    assert str(message["Subject"]) == "Ready ✓"
    assert message.get_content().replace("\r\n", "\n") == "Hello\n.leading dot\n"


@pytest.mark.asyncio
async def test_smtp_rejection_does_not_report_delivery(email_adapter, smtp_sink):
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        await email_adapter.send_email(to="rejected@example.test", subject="Rejected", body="No delivery")
    assert smtp_sink == []


@pytest.mark.asyncio
async def test_starttls_is_required_by_default_without_plaintext_fallback(email_adapter, smtp_sink, monkeypatch):
    monkeypatch.delenv("SMTP_SECURITY")
    with pytest.raises(smtplib.SMTPNotSupportedError):
        await email_adapter.send_email(to="recipient@example.test", subject="TLS", body="No downgrade")
    assert smtp_sink == []


@pytest.mark.asyncio
@pytest.mark.parametrize("security", ["starttls", "ssl"])
async def test_secure_transport_authenticates_after_tls(email_adapter, monkeypatch, security):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")
    monkeypatch.setenv("SMTP_SECURITY", security)
    monkeypatch.setenv("SMTP_USER", "test-user")
    monkeypatch.setenv("SMTP_PASSWORD", "test-only-password")
    client = MagicMock()
    smtp = client.return_value.__enter__.return_value
    smtp.send_message.return_value = {}
    monkeypatch.setattr(email_adapter.smtplib, "SMTP_SSL" if security == "ssl" else "SMTP", client)
    assert await email_adapter.send_email(to="recipient@example.test", subject="TLS", body="Body") is True
    assert client.call_args.kwargs["timeout"] == 15
    smtp.login.assert_called_once_with("test-user", "test-only-password")
    if security == "ssl":
        assert client.call_args.kwargs["context"].check_hostname
        smtp.starttls.assert_not_called()
    else:
        assert smtp.starttls.call_args.kwargs["context"].check_hostname
        assert [call[0] for call in smtp.method_calls] == ["starttls", "login", "send_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("update,arguments", [
    ({"SMTP_HOST": ""}, {}),
    ({"SMTP_HOST": "smtp.example.test"}, {}),
    ({"SMTP_USER": "test-user", "SMTP_PASSWORD": "test-only-password"}, {}),
    ({"SMTP_USER": "test-user"}, {}),
    ({"SMTP_SECURITY": "unknown"}, {}),
    ({"SMTP_PORT": "0"}, {}),
    ({}, {"to": "recipient@example.test\r\nBcc: other@example.test"}),
    ({}, {"subject": "Hello\r\nBcc: other@example.test"}),
    ({}, {"template_id": "provider-template"}),
    ({}, {"template_data": {"name": "User"}}),
])
async def test_invalid_configuration_or_headers_never_send(email_adapter, smtp_sink, monkeypatch, update, arguments):
    for key, value in update.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        await email_adapter.send_email(**{"to": "recipient@example.test", "subject": "Subject", "body": "Body", **arguments})
    assert smtp_sink == []


@pytest.mark.asyncio
async def test_router_calls_smtp_only_after_app_owned_recipient_and_opt_in_check(email_adapter, smtp_sink):
    records = {
        ("app-a", "alice", "allowed"): {"email": "alice@example.test", "email_enabled": True},
        ("app-a", "alice", "opted-out"): {"email": "alice@example.test", "email_enabled": False},
    }

    class FixtureHandler:
        async def on_notice(self, ctx, *, record_id):
            record = records.get((ctx.app_id, ctx.user_id, record_id))
            if record is None:
                return {"success": False, "error_code": "NOTICE_NOT_OWNED"}
            if not record["email_enabled"]:
                return {"success": True, "delivery": "opted_out"}
            return await email_adapter.send_email(to=record["email"], subject="Owned notice", body="Ready")

    module = SimpleNamespace(
        name="fixture_notices", handler=FixtureHandler(), definition=None,
        manifests=SimpleNamespace(
            events=None, notifications=None,
            reactions=ModuleReactionsManifest.model_validate({
                "schema_version": "mozaiks.reactions.v1", "reactions": [{
                    "id": "fixture_notices.email", "event_type": "domain.fixture.notice_ready",
                    "target": {"kind": "handler", "handler_method": "on_notice"},
                }],
            }),
        ),
    )
    router = ModuleEventRouter([module])

    async def dispatch(app="app-a", user="alice", record_id="allowed", **payload):
        return await router.handle_event("domain.fixture.notice_ready", {
            "id": f"{app}-{user}-{record_id}", "type": "domain.fixture.notice_ready",
            "tenant": {"app_id": app}, "actor": {"type": "user", "id": user},
            "payload": {"record_id": record_id, **payload},
        })

    assert (await dispatch()).success
    assert len(smtp_sink) == 1 and str(smtp_sink[0]["To"]) == "alice@example.test"
    assert (await dispatch(record_id="opted-out")).success
    assert not (await dispatch(app="app-b")).success
    assert not (await dispatch(user="bob")).success
    assert not (await dispatch(to="attacker@example.test")).success
    assert len(smtp_sink) == 1
