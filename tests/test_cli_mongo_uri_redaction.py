"""What the MongoDB preflight error prints of MONGO_URI, and what it never prints.

`mozaiks serve` is also the generated-app container entrypoint, so its
MongoDB preflight error lands in container logs. The driver reads a malformed
URI leniently (an unescaped ``/`` in a password turns the text before it into
a host and a port), so both the URI shown and the driver's own message are
checked here, through the real command and the real driver.

The guarantee: while the URI still has the ``@`` that ends its credential
section, no text of that section and no text of the query string is printed,
whatever characters the password contains. Without that ``@`` (the URI is cut
off, or the ``@`` was percent-encoded) what remains is a different URI whose
host is password text; the tests at the end pin which of those shapes are
hidden and which one cannot be told apart from a real address.
"""

from __future__ import annotations

import random
import socket
import warnings
from typing import NamedTuple

import pytest

from mozaiks_cli import mongo_preflight, studio_launcher
from tests.test_cli_serve_first_run import _VALID_CONTRACT, _serve, _workspace

_NOT_SHOWN = "<not shown: not a well-formed MongoDB URI>"
_HOST_NOT_SHOWN = "<host not shown>"


class _Case(NamedTuple):
    name: str
    uri: str
    shown: str  # the only form `redact_mongo_uri` may return
    secrets: tuple[str, ...]  # text that must never be printed
    printed: str | None = None  # what the commands print, when the driver's verdict changes it


_CASES: list[_Case] = [
    _Case("no_credentials", "mongodb://127.0.0.1:{port}/app", "mongodb://127.0.0.1:{port}/app", ()),
    _Case(
        "credentials",
        "mongodb://appuser:PlainSecret@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "PlainSecret"),
    ),
    _Case(
        "srv",
        "mongodb+srv://appuser:SrvSecret@cluster0.example.invalid/app?retryWrites=true",
        "mongodb+srv://***@cluster0.example.invalid/app",
        ("appuser", "SrvSecret", "retryWrites"),
    ),
    _Case(
        "at_in_password",
        "mongodb://appuser:AtHead@AtTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "AtHead", "AtTail"),
    ),
    _Case(
        "slash_in_password",
        "mongodb://appuser:SlashHead/SlashTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "SlashHead", "SlashTail"),
    ),
    _Case(
        # The driver takes "appuser" for a host and the digits for its port.
        "digits_then_slash_in_password",
        "mongodb://appuser:40404/DigitTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "40404", "DigitTail"),
    ),
    _Case(
        # The driver rejects a second ":" in the credential section.
        "colon_in_password",
        "mongodb://appuser:ColonHead:ColonTail@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "ColonHead", "ColonTail"),
        printed=f"mongodb://{_NOT_SHOWN}",
    ),
    _Case(
        "question_mark_in_password",
        "mongodb://appuser:QuestHead?QuestTail@127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "QuestHead", "QuestTail"),
    ),
    _Case(
        "hash_in_password",
        "mongodb://appuser:HashHead#HashTail@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "HashHead", "HashTail"),
    ),
    _Case(
        "credentials_in_query",
        "mongodb://127.0.0.1:{port}/app?authSource=admin&password=QuerySecret",
        "mongodb://127.0.0.1:{port}/app",
        ("authSource", "QuerySecret"),
    ),
    _Case(
        # The driver warns about an unusable option by quoting its value.
        "option_value",
        "mongodb://appuser:OptSecret@127.0.0.1:{port}/app?readPreference=PrefSecret",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "OptSecret", "PrefSecret"),
    ),
    _Case(
        "at_in_query",
        "mongodb://appuser:AppSecret@127.0.0.1:{port}/app?appName=me@AtCorp",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "AppSecret", "AtCorp"),
    ),
    _Case(
        "percent_encoded_credentials",
        "mongodb://appuser:Enc%40Head%2FEncTail@127.0.0.1:{port}/app",
        "mongodb://***@127.0.0.1:{port}/app",
        ("appuser", "Enc%40Head", "Enc@Head", "EncTail"),
    ),
    _Case(
        "ipv6_host",
        "mongodb://appuser:V6Secret@[::1]:{port}/app",
        "mongodb://***@[::1]:{port}/app",
        ("appuser", "V6Secret"),
    ),
    _Case(
        "replica_set",
        "mongodb://appuser:RsSecret@127.0.0.1:{port},127.0.0.1:{other_port}/app?replicaSet=rs0",
        "mongodb://***@127.0.0.1:{port},127.0.0.1:{other_port}/app",
        ("appuser", "RsSecret", "replicaSet"),
    ),
    _Case(
        "socket_path",
        "mongodb://appuser:SockSecret@%2Ftmp%2Fno-such-mongodb.sock/app",
        "mongodb://***@%2Ftmp%2Fno-such-mongodb.sock/app",
        ("appuser", "SockSecret"),
    ),
    _Case(
        # The "@" that was percent-encoded is the one that ends the credentials,
        # not the one in the password. Older drivers decode it and dial
        # "wrongtail@127.0.0.1"; newer ones reject the URI.
        "wrong_at_percent_encoded",
        "mongodb://appuser:WrongHead@WrongTail%40127.0.0.1:{port}/app",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "WrongHead", "WrongTail"),
    ),
    _Case(
        # As above with a "/" in the password: "appuser" reads as a host, the
        # digits as its port and the rest as a database name.
        "wrong_at_percent_encoded_after_a_slash",
        "mongodb://appuser:40404/RestTail%40127.0.0.1:{port}",
        f"mongodb://{_NOT_SHOWN}",
        ("appuser", "40404", "RestTail"),
    ),
    _Case(
        # The URI ends before the real "@host": an unquoted value cut at " #"
        # in a .env file, or at "&" or ";" in a shell. What is left is a
        # well-formed URI whose host is the rest of the password.
        "cut_off_after_at_in_password",
        "mongodb://appuser:CutHead@CutTail",
        f"mongodb://***@{_HOST_NOT_SHOWN}",
        ("appuser", "CutHead", "CutTail"),
    ),
    _Case(
        "cut_off_after_at_and_slash_in_password",
        "mongodb://appuser:CutHead@CutTail/CutDbTail",
        f"mongodb://***@{_HOST_NOT_SHOWN}",
        ("appuser", "CutHead", "CutTail", "CutDbTail"),
    ),
    _Case(
        "srv_cut_off_after_at_in_password",
        "mongodb+srv://appuser:CutHead@CutTail",
        f"mongodb+srv://***@{_HOST_NOT_SHOWN}",
        ("appuser", "CutHead", "CutTail"),
    ),
    _Case(
        # The cost of hiding that shape: a real host that has it is hidden too.
        "bare_host_with_credentials",
        "mongodb://appuser:BareSecret@no-such-mongo-host/app",
        f"mongodb://***@{_HOST_NOT_SHOWN}",
        ("appuser", "BareSecret", "no-such-mongo-host"),
    ),
    _Case(
        "bare_host_and_port_with_credentials",
        "mongodb://appuser:PortSecret@no-such-mongo-host:{port}/app",
        "mongodb://***@no-such-mongo-host:{port}/app",
        ("appuser", "PortSecret"),
    ),
    _Case(
        "bare_host_without_credentials",
        "mongodb://no-such-mongo-host/app",
        "mongodb://no-such-mongo-host/app",
        (),
    ),
    _Case(
        # The driver refuses the port: its reading of the URI is not ours.
        "port_out_of_range",
        "mongodb://appuser:RangeSecret@127.0.0.1:99999/app",
        "mongodb://***@127.0.0.1:99999/app",
        ("appuser", "RangeSecret"),
        printed=f"mongodb://{_NOT_SHOWN}",
    ),
    _Case(
        # The driver read the URI and refuses what it asks for: hosts stay shown.
        "conflicting_options",
        "mongodb://appuser:ConfSecret@127.0.0.1:{port},127.0.0.1:{other_port}/app?directConnection=true",
        "mongodb://***@127.0.0.1:{port},127.0.0.1:{other_port}/app",
        ("appuser", "ConfSecret", "directConnection"),
    ),
    _Case("not_a_uri", "hunter2-not-a-uri", _NOT_SHOWN, ("hunter2",)),
    _Case("empty", "", _NOT_SHOWN, ()),
]
_COMMAND_CASES = [case for case in _CASES if case.name != "empty"]  # an empty MONGO_URI is "not configured"
_LAUNCHER_CASES = [
    case
    for case in _CASES
    if case.name
    in {
        "at_in_password",
        "slash_in_password",
        "credentials_in_query",
        "colon_in_password",
        "wrong_at_percent_encoded",
        "cut_off_after_at_in_password",
        "cut_off_after_at_and_slash_in_password",
    }
]


def _ids(cases: list[_Case]) -> list[str]:
    return [case.name for case in cases]


@pytest.fixture
def closed_ports() -> dict[str, int]:
    """Two loopback ports nothing listens on."""
    while True:
        with socket.socket() as first, socket.socket() as second:
            first.bind(("127.0.0.1", 0))
            second.bind(("127.0.0.1", 0))
            ports = {"port": first.getsockname()[1], "other_port": second.getsockname()[1]}
        # 40404 is credential text in two cases; keep it out of the expected output.
        if 40404 not in ports.values():
            return ports


def _assert_nothing_secret(text: str, secrets: tuple[str, ...]) -> None:
    for secret in secrets:
        assert secret not in text, f"{secret!r} was printed in: {text}"


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_redacted_uri_shows_hosts_and_never_credentials_or_options(case, closed_ports) -> None:
    redacted = mongo_preflight.redact_mongo_uri(case.uri.format(**closed_ports))

    assert redacted == case.shown.format(**closed_ports)
    _assert_nothing_secret(redacted, case.secrets)


@pytest.mark.parametrize("case", _COMMAND_CASES, ids=_ids(_COMMAND_CASES))
def test_serve_never_prints_credential_text_from_mongo_uri(
    case, closed_ports, monkeypatch, tmp_path, capsys
) -> None:
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    monkeypatch.setenv("MONGO_URI", case.uri.format(**closed_ports))
    monkeypatch.setenv(mongo_preflight.PREFLIGHT_TIMEOUT_ENV, "1000")

    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        with pytest.raises(SystemExit) as exc:
            _serve(monkeypatch, workspace)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    printed = captured.out + captured.err + "".join(str(warning.message) for warning in raised)
    shown = (case.printed or case.shown).format(**closed_ports)
    assert f"MongoDB is not reachable at MONGO_URI ({shown})." in captured.out
    assert "Underlying error: " in captured.out
    _assert_nothing_secret(printed, case.secrets)


@pytest.mark.parametrize("case", _LAUNCHER_CASES, ids=_ids(_LAUNCHER_CASES))
def test_studio_launcher_never_prints_credential_text_from_mongo_uri(case, closed_ports, tmp_path) -> None:
    env = {"MONGO_URI": case.uri.format(**closed_ports), mongo_preflight.PREFLIGHT_TIMEOUT_ENV: "1000"}

    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError) as exc:
            studio_launcher._assert_mongo_ready(env, workspace_root=tmp_path)

    message = str(exc.value)
    shown = (case.printed or case.shown).format(**closed_ports)
    assert f"Could not connect to MONGO_URI ({shown})." in message
    _assert_nothing_secret(message + "".join(str(warning.message) for warning in raised), case.secrets)


def test_serve_hides_the_password_text_an_inline_comment_leaves_in_dotenv(monkeypatch, tmp_path, capsys) -> None:
    """python-dotenv cuts an unquoted value at `` #``. With an unescaped ``@``
    before it, what is left is a URI whose host is the rest of the password."""
    workspace = _workspace(tmp_path, secrets=_VALID_CONTRACT)
    kept_by_dotenv = "mongodb://appuser:EnvHead@EnvTail"
    (workspace / ".env").write_text(
        f"MONGO_URI={kept_by_dotenv} #EnvRest@127.0.0.1:27017/app\n", encoding="utf-8"
    )
    # serve() loads the workspace .env into os.environ; record a restore entry first.
    monkeypatch.setenv("MONGO_URI", "")
    monkeypatch.delenv("MONGO_URI")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv(mongo_preflight.PREFLIGHT_TIMEOUT_ENV, "1000")

    with pytest.raises(SystemExit) as exc:
        _serve(monkeypatch, workspace)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert f"MongoDB is not reachable at MONGO_URI (mongodb://***@{_HOST_NOT_SHOWN})." in captured.out
    _assert_nothing_secret(
        captured.out + captured.err, ("appuser", "EnvHead", "EnvTail", "envtail", "EnvRest", "127.0.0.1")
    )


def test_a_driver_message_about_a_misread_uri_is_withheld(monkeypatch) -> None:
    """The driver took part of the password for a host and port and names them."""

    def fail_ping(uri: str, *, timeout_ms: int) -> None:
        raise RuntimeError("appuser:40404: [Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", fail_ping)

    failure = mongo_preflight.mongo_unreachable(
        "mongodb://appuser:40404/DigitTail@127.0.0.1:27017/app", timeout_ms=1000
    )

    assert failure is not None
    assert failure.shown_uri == f"mongodb://{_NOT_SHOWN}"
    assert failure.reason.startswith("RuntimeError: the driver's message is not shown")
    _assert_nothing_secret(failure.reason, ("appuser", "40404", "DigitTail", "getaddrinfo"))


def test_a_driver_message_that_names_a_host_not_shown_is_withheld(monkeypatch) -> None:
    """Given time, the driver names the host it could not resolve: here, the
    rest of the password."""

    def fail_ping(uri: str, *, timeout_ms: int) -> None:
        raise RuntimeError("cuttail:27017: [Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(mongo_preflight, "ping_mongo_uri", fail_ping)

    failure = mongo_preflight.mongo_unreachable("mongodb://appuser:CutHead@CutTail", timeout_ms=1000)

    assert failure is not None
    assert failure.shown_uri == f"mongodb://***@{_HOST_NOT_SHOWN}"
    assert failure.reason.startswith("RuntimeError: the driver's message is not shown because it names the host.")
    assert "write its port" in failure.reason
    assert "percent-encode" in failure.reason
    _assert_nothing_secret(
        failure.shown_uri + failure.reason, ("appuser", "CutHead", "CutTail", "cuttail", "getaddrinfo")
    )


def test_a_uri_the_driver_rejects_is_reported_without_the_driver_message() -> None:
    failure = mongo_preflight.mongo_unreachable(
        "mongodb://appuser:AtHead@AtTail@127.0.0.1:27017/app", timeout_ms=1000
    )

    assert failure is not None
    assert failure.shown_uri == f"mongodb://{_NOT_SHOWN}"
    assert failure.reason.startswith("InvalidURI: the MongoDB driver rejected MONGO_URI before connecting")
    assert "percent-encoded" in failure.reason
    _assert_nothing_secret(failure.reason, ("appuser", "AtHead", "AtTail"))


@pytest.mark.parametrize(
    ("uri", "driver_error"),
    [
        ("mongodb://appuser:ColonHead:ColonTail@127.0.0.1:27017/app", "InvalidURI"),
        ("mongodb://appuser:RangeSecret@127.0.0.1:99999/app", "ValueError"),
        ("mongodb://appuser:TlsSecret@127.0.0.1:27017/app?tls=true&ssl=false", "InvalidURI"),
    ],
    ids=["second_colon", "port_out_of_range", "contradicting_options"],
)
def test_hosts_are_not_shown_for_a_uri_whose_form_the_driver_rejects(uri, driver_error) -> None:
    """Each of these reads as a host after one ``@``, and the driver disagrees
    that the URI is well formed: its verdict wins."""
    assert mongo_preflight.redact_mongo_uri(uri).startswith("mongodb://***@127.0.0.1:")

    failure = mongo_preflight.mongo_unreachable(uri, timeout_ms=1000)

    assert failure is not None
    assert failure.shown_uri == f"mongodb://{_NOT_SHOWN}"
    assert failure.reason.startswith(f"{driver_error}: the MongoDB driver rejected MONGO_URI before connecting")
    assert "127.0.0.1" not in failure.reason


def test_hosts_stay_shown_when_the_driver_read_the_uri_and_refused_its_request() -> None:
    """A failed SRV lookup, like conflicting options, is about a URI the driver
    could read. The operator needs the host name to see which lookup failed."""
    failure = mongo_preflight.mongo_unreachable(
        "mongodb+srv://appuser:SrvSecret@cluster0.example.invalid/app?retryWrites=true", timeout_ms=1000
    )

    assert failure is not None
    assert failure.shown_uri == "mongodb+srv://***@cluster0.example.invalid/app"
    assert failure.reason.startswith(
        "ConfigurationError: the MongoDB driver rejected MONGO_URI before connecting"
    )
    _assert_nothing_secret(failure.shown_uri + failure.reason, ("appuser", "SrvSecret", "retryWrites"))


def test_a_connection_failure_to_a_well_formed_uri_keeps_the_driver_message(closed_ports) -> None:
    """Redaction must not cost the operator the one useful line: which host refused."""
    port = closed_ports["port"]

    failure = mongo_preflight.mongo_unreachable(
        f"mongodb://appuser:PlainSecret@127.0.0.1:{port}/app", timeout_ms=1000
    )

    assert failure is not None
    assert failure.shown_uri == f"mongodb://***@127.0.0.1:{port}/app"
    assert failure.reason.startswith("ServerSelectionTimeoutError: ")
    assert "not shown" not in failure.reason
    _assert_nothing_secret(failure.reason, ("appuser", "PlainSecret"))


# Unescaped passwords, drawn from characters that move text around in a URI.
# The letters appear in none of the hosts, database names or placeholders below.
_PASSWORD_CHARACTERS = "QXZqxz0123456789@@@:/?#%&;=+,. -_[]!$"


def _unescaped_passwords(count: int) -> list[str]:
    rng = random.Random(797)
    return ["".join(rng.choices(_PASSWORD_CHARACTERS, k=rng.randint(1, 16))) for _ in range(count)]


@pytest.mark.parametrize(
    ("template", "shown"),
    [
        ("mongodb://admin:{password}@127.0.0.1:27999/app", "mongodb://***@127.0.0.1:27999/app"),
        ("mongodb://admin:{password}@127.0.0.1:27999", "mongodb://***@127.0.0.1:27999"),
        ("mongodb://{password}@127.0.0.1:27999/app", "mongodb://***@127.0.0.1:27999/app"),
        ("mongodb://admin:{password}@[::1]:27999/app", "mongodb://***@[::1]:27999/app"),
        ("mongodb://admin:{password}@db.example.invalid/app?authSource=admin", "mongodb://***@db.example.invalid/app"),
        (
            "mongodb://admin:{password}@127.0.0.1:27999,127.0.0.1:27998/app?replicaSet=rs0",
            "mongodb://***@127.0.0.1:27999,127.0.0.1:27998/app",
        ),
        ("mongodb+srv://admin:{password}@cluster0.example.invalid/app", "mongodb+srv://***@cluster0.example.invalid/app"),
        ("mongodb://admin:{password}@mongo:27017/app", "mongodb://***@mongo:27017/app"),
        ("mongodb://admin:{password}@mongo/app", f"mongodb://***@{_HOST_NOT_SHOWN}"),
    ],
)
def test_no_unescaped_password_is_printed_while_the_uri_has_its_host_separator(template, shown) -> None:
    """Whatever the password contains, the URI prints as its redacted form or
    as the placeholder, and as nothing else."""
    scheme = template.split("//", 1)[0] + "//"
    allowed = {shown, f"{scheme}{_NOT_SHOWN}"}

    for password in _unescaped_passwords(4000):
        redacted = mongo_preflight.redact_mongo_uri(template.format(password=password))
        assert redacted in allowed, f"password {password!r} printed as {redacted!r}"


def test_no_text_is_printed_after_a_percent_encoded_host_separator() -> None:
    """``%40`` where the credentials end is never read as part of a host name."""
    for password in _unescaped_passwords(4000):
        if "/" in password or "?" in password:
            continue  # those cut the URI short of the %40: the cut-off shapes below
        redacted = mongo_preflight.redact_mongo_uri(f"mongodb://admin:{password}%40127.0.0.1:27999/app")
        assert redacted == f"mongodb://{_NOT_SHOWN}", f"password {password!r} printed as {redacted!r}"


def test_a_cut_off_uri_never_prints_a_password_tail_that_has_no_dot_and_no_port() -> None:
    rng = random.Random(797)
    allowed = {f"mongodb://***@{_HOST_NOT_SHOWN}", f"mongodb://{_NOT_SHOWN}"}

    for _ in range(4000):
        tail = "".join(rng.choices("QXZqxz0123456789-_", k=rng.randint(1, 16)))
        for uri in (f"mongodb://admin:Head@{tail}", f"mongodb://admin:Head@{tail}/DbTail"):
            redacted = mongo_preflight.redact_mongo_uri(uri)
            assert redacted in allowed, f"{uri!r} printed as {redacted!r}"


@pytest.mark.parametrize(
    "not_an_address",
    [".LeadTail", "TrailTail.", "Two..Tail", "-DashTail", "DashTail-", "Pct%2ETail", "[fe80::1%25ZoneTail]"],
)
def test_text_that_does_not_read_as_an_address_is_never_printed_as_a_host(not_an_address) -> None:
    for uri in (
        f"mongodb://appuser:Head@{not_an_address}",
        f"mongodb://appuser:Head@{not_an_address}:27017/app",
        f"mongodb://{not_an_address}",
    ):
        assert mongo_preflight.redact_mongo_uri(uri) == f"mongodb://{_NOT_SHOWN}"


def test_a_cut_off_uri_whose_remaining_text_reads_as_an_address_is_printed() -> None:
    """The limit of this redaction, stated in the changelog. A URI that ends
    before its real ``@host`` is a well-formed URI for another host. After an
    unescaped ``@``, a password tail with a dot or a port is what real
    addresses look like; with no ``@`` left, so is the user name, with a
    password of digits as its port."""
    cut_off_after_at = "mongodb://appuser:CutHead@"
    assert mongo_preflight.redact_mongo_uri(cut_off_after_at + "cut.tail") == "mongodb://***@cut.tail"
    assert mongo_preflight.redact_mongo_uri(cut_off_after_at + "CutTail:1234") == "mongodb://***@CutTail:1234"
    assert mongo_preflight.redact_mongo_uri("mongodb://appuser:40404") == "mongodb://appuser:40404"
