from __future__ import annotations

from tests.import_utils import import_module_directly

_consumer_mod = import_module_directly("mozaiksai.core.automation.nats_consumer")
_publisher_mod = import_module_directly("mozaikscore.core.automation_nats")


def test_default_transport_mode_is_http(monkeypatch) -> None:
    monkeypatch.delenv("MOZAIKS_AUTOMATION_TRANSPORT", raising=False)
    assert _publisher_mod.get_automation_transport_mode() == "http"
    assert _publisher_mod.use_http_transport() is True
    assert _publisher_mod.use_nats_transport() is False


def test_nats_transport_mode_flags(monkeypatch) -> None:
    monkeypatch.setenv("MOZAIKS_AUTOMATION_TRANSPORT", "nats")
    assert _publisher_mod.get_automation_transport_mode() == "nats"
    assert _publisher_mod.use_nats_transport() is True
    assert _publisher_mod.use_http_transport() is False
    assert _consumer_mod.use_nats_transport() is True


def test_build_subject_sanitizes_app_id(monkeypatch) -> None:
    monkeypatch.setenv("MOZAIKS_AUTOMATION_NATS_SUBJECT_PREFIX", "mozaiks.substrate.events")
    subject = _publisher_mod.build_subject("my.app")
    assert subject == "mozaiks.substrate.events.my_app"


def test_consumer_subscription_subject_uses_wildcard(monkeypatch) -> None:
    monkeypatch.setenv("MOZAIKS_AUTOMATION_NATS_SUBJECT_PREFIX", "mozaiks.substrate.events")
    subject = _consumer_mod.build_subscription_subject()
    assert subject == "mozaiks.substrate.events.*"
