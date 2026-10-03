"""MONGO_URI is never printed with credential text, however it is malformed.

`mozaiks serve` is also the generated-app container entrypoint, so its
MongoDB preflight error lands in container logs. The driver reads a malformed
URI leniently (an unescaped ``/`` in a password turns the text before it into
a host and a port), so both the URI shown and the driver's own message are
checked here, through the real command and the real driver.
"""

from __future__ import annotations

import socket
import warnings

import pytest

from mozaiks_cli import mongo_preflight, studio_launcher
from tests.test_cli_serve_first_run import _VALID_CONTRACT, _serve, _workspace

_NOT_SHOWN = "<not shown: not a well-formed MongoDB URI>"

# (id, MONGO_URI, the only form that may be printed, text that must never be printed)
_CASES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("no_credentials", "mongodb://127.0.0.1:{port}/app", "mongodb://127.0.0.1:{port}/app", ()),
    (
        "credentials",
        "mongodb://appuser:PlainSecret@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "PlainSecret"),
    ),
    (
        "srv",
        "mongodb+srv://appuser:SrvSecret@cluster0.example.invalid/app?retryWrites=true",
        "mongodb+srv://***@cluster0.example.invalid/app",
        ("appuser", "SrvSecret", "retryWrites"),
    ),
    (
        "at_in_password",
        "mongodb://appuser:AtHead@AtTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "AtHead", "AtTail"),
    ),
    (
        "slash_in_password",
        "mongodb://appuser:SlashHead/SlashTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "SlashHead", "SlashTail"),
    ),
    (
        # The driver takes "appuser" for a host and the digits for its port.
        "digits_then_slash_in_password",
        "mongodb://appuser:40404/DigitTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "40404", "DigitTail"),
    ),
    (
        "colon_in_password",
        "mongodb://appuser:ColonHead:ColonTail@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "ColonHead", "ColonTail"),
    ),
    (
        "question_mark_in_password",
        "mongodb://appuser:QuestHead?QuestTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "QuestHead", "QuestTail"),
    ),
    (
        "hash_in_password",
        "mongodb://appuser:HashHead#HashTail@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "HashHead", "HashTail"),
    ),
    (
        "credentials_in_query",
        "mongodb://127.0.0.1:{port}/app?authSource=admin&password=QuerySecret",
        "mongodb://127.0.0.1:{port}/app",
        ("authSource", "QuerySecret"),
    ),
    (
        # The driver warns about an unusable option by quoting its value.
        "option_value",
        "mongodb://appuser:OptSecret@127.0.0.1:{port}/app?readPreference=PrefSecret",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "OptSecret", "PrefSecret"),
    ),
    (
        "at_in_query",
        "mongodb://appuser:AppSecret@127.0.0.1:{port}/app?appName=me@AtCorp",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "AppSecret", "AtCorp"),
    ),
    (
        "percent_encoded_credentials",
        "mongodb://appuser:Enc%40Head%2FEncTail@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "Enc%40Head", "Enc@Head", "EncTail"),
    ),
    (
        "ipv6_host",
        "mongodb://appuser:V6Secret@[::1]:{port}/app",
        "mongodb://***@[::1]:{port}/app",
        ("appuser", "V6Secret"),
    ),
    (
        "replica_set",
        "mongodb://appuser:RsSecret@127.0.0.1:{port},127.0.0.1:{other_port}/app?replicaSet=rs0",
        "mongodb://***@127.0.0.1:{port},127.0.0.1:{other_port}/app",
        ("appuser", "RsSecret", "replicaSet"),
    ),
    ("not_a_uri", "hunter2-not-a-uri", _NOT_SHOWN, ("hunter2",)),
    ("empty", "", _NOT_SHOWN, ()),
]
_CASE_IDS = [case[0] for case in _CASES]


@pytest.fixture
def closed_ports() -> dict[str, int]:
    """Two loopback ports nothing listens on."""
    while True:
        with socket.socket() as first, socket.socket() as second:
            first.bind(("127.0.0.1", 0))
            second.bind(("127.0.0.1", 0))
            ports = {"port": first.getsockname()[1], "other_port": second.getsockname()[1]}
        # 40404 is credential text in one case; keep it out of the expected output.
        if 40404 not in ports.values():
            return ports


def _assert_nothing_secret(text: str, secrets: tuple[str, ...]) -> None:
    for secret in secrets:
        assert secret not in text, f"{secret!r} was printed in: {text}"


@pytest.mark.parametrize(("_name", "uri", "shown", "secrets"), _CASES, ids=_CASE_IDS)
def test_redacted_uri_shows_hosts_and_never_credentials_or_options(
    _name, uri, shown, secrets, closed_ports
) -> None:
    redacted = mongo_preflight.redact_mongo_uri(uri.format(**closed_ports))

    assert redacted == shown.format(**closed_ports)
    _assert_nothing_secret(redacted, secrets)


@pytest.mark.parametrize(
    ("_name", "uri", "shown", "secrets"),
    [case for case in _CASES if case[0] != "empty"],  # an empty MONGO_URI is "not configured"
    ids=[name for name in _CASE_IDS if name != "empty"],
)
def test_serve_never_prints_credential_text_from_mongo_uri(
    _name, uri, shown, secrets, closed_ports, monkeypatch, tmp_path, capsys
) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    monkeypatch.setenv("MONGO_URI", uri.format(**closed_ports))
    monkeypatch.setenv(mongo_preflight.PREFLIGHT_TIMEOUT_ENV, "1000")

    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        with pytest.raises(SystemExit) as exc:
            _serve(monkeypatch, workspace)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    printed = captured.out + captured.err + "".join(str(warning.message) for warning in raised)
    assert f"MongoDB is not reachable at MONGO_URI ({shown.format(**closed_ports)})." in captured.out
    assert "Underlying error: " in captured.out
    _assert_nothing_secret(printed, secrets)


@pytest.mark.parametrize(
    ("_name", "uri", "shown", "secrets"),
    [case for case in _CASES if case[0] in {"at_in_password", "slash_in_password", "credentials_in_query"}],
    ids=["at_in_password", "slash_in_password", "credentials_in_query"],
)
def test_studio_launcher_never_prints_credential_text_from_mongo_uri(
    _name, uri, shown, secrets, closed_ports, tmp_path
) -> None:
    env = {"MONGO_URI": uri.format(**closed_ports), mongo_preflight.PREFLIGHT_TIMEOUT_ENV: "1000"}

    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError) as exc:
            studio_launcher._assert_mongo_ready(env, workspace_root=tmp_path)

    message = str(exc.value)
    assert f"Could not connect to MONGO_URI ({shown.format(**closed_ports)})." in message
    _assert_nothing_secret(message + "".join(str(warning.message) for warning in raised), secrets)


def test_a_driver_message_about_a_misread_uri_is_withheld(monkeypatch) -> None:
    """The driver took part of the password for a host and port and names them."""

    def fail_ping(uri: str, *, timeout_ms: int) -> None:
        raise RuntimeError("appuser:40404: [Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", fail_ping)

    reason = mongo_preflight.mongo_unreachable_reason(
        "mongodb://appuser:40404/DigitTail@127.0.0.1:27017/app", timeout_ms=1000
    )

    assert reason is not None
    assert reason.startswith("RuntimeError: the driver's message is not shown")
    _assert_nothing_secret(reason, ("appuser", "40404", "DigitTail", "getaddrinfo"))


def test_a_uri_the_driver_rejects_is_reported_without_the_driver_message() -> None:
    reason = mongo_preflight.mongo_unreachable_reason(
        "mongodb://appuser:AtHead@AtTail@127.0.0.1:27017/app", timeout_ms=1000
    )

    assert reason is not None
    assert reason.startswith("InvalidURI: the MongoDB driver rejected MONGO_URI before connecting")
    assert "percent-encoded" in reason
    _assert_nothing_secret(reason, ("appuser", "AtHead", "AtTail"))


def test_a_connection_failure_to_a_well_formed_uri_keeps_the_driver_message(closed_ports) -> None:
    """Redaction must not cost the operator the one useful line: which host refused."""
    port = closed_ports["port"]

    reason = mongo_preflight.mongo_unreachable_reason(
        f"mongodb://appuser:PlainSecret@127.0.0.1:{port}/app", timeout_ms=1000
    )

    assert reason is not None
    assert reason.startswith("ServerSelectionTimeoutError: ")
    assert "not shown" not in reason
    _assert_nothing_secret(reason, ("appuser", "PlainSecret"))
