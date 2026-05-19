"""Tests for the generic infrastructure probe adapters.

dns_probe, tls_probe, and http_health are all network-free in this suite:
- dns_probe: tested via sync helper with socket.getaddrinfo patched
- tls_probe: result shape and helper logic only (no live TLS connections)
- http_health: tested via httpx.MockTransport (no real HTTP calls)
"""

from __future__ import annotations

import asyncio
import importlib.util
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest


WORKSPACE = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# DnsProbeResult shape
# ---------------------------------------------------------------------------


class TestDnsProbeResultShape:
    def test_to_dict_has_all_fields(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/dns_probe.py", "tests.dns_probe_shape")
        result = mod.DnsProbeResult(
            hostname="example.com",
            success=True,
            a_records=["93.184.216.34"],
            aaaa_records=[],
            mx_records=["mail.example.com"],
            ns_records=["a.iana-servers.net"],
            cname=None,
            txt_records=[],
            resolution_ms=12.5,
            error=None,
        )
        d = result.to_dict()
        assert d["hostname"] == "example.com"
        assert d["success"] is True
        assert d["a_records"] == ["93.184.216.34"]
        assert d["mx_records"] == ["mail.example.com"]
        assert d["resolution_ms"] == 12.5
        assert d["error"] is None

    def test_failed_result_has_error_and_empty_records(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/dns_probe.py", "tests.dns_probe_fail")
        result = mod.DnsProbeResult(
            hostname="nonexistent.invalid",
            success=False,
            a_records=[],
            aaaa_records=[],
            mx_records=[],
            ns_records=[],
            cname=None,
            txt_records=[],
            resolution_ms=500.0,
            error="DNS resolution failed: [Errno 11001] getaddrinfo failed",
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] is not None
        assert d["a_records"] == []


class TestDnsProbeSync:
    def test_successful_resolution_produces_a_records(self) -> None:
        """_probe_dns_sync returns a_records when socket.getaddrinfo succeeds."""
        mod = _load_module("mozaiksai/core/adapters/dns_probe.py", "tests.dns_probe_sync")

        fake_infos = [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("2606:2800:220:1:248:1893:25c8:1946", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            result = mod._probe_dns_sync("example.com")

        assert result.success is True
        assert "93.184.216.34" in result.a_records
        assert "2606:2800:220:1:248:1893:25c8:1946" in result.aaaa_records
        assert result.error is None

    def test_resolution_failure_sets_success_false(self) -> None:
        """_probe_dns_sync sets success=False when getaddrinfo raises."""
        import socket as _socket
        mod = _load_module("mozaiksai/core/adapters/dns_probe.py", "tests.dns_probe_fail_sync")

        with patch("socket.getaddrinfo", side_effect=_socket.gaierror("Name or service not known")):
            result = mod._probe_dns_sync("nonexistent.invalid")

        assert result.success is False
        assert result.a_records == []
        assert result.error is not None
        assert "DNS resolution failed" in result.error

    def test_hostname_stripped_and_lowercased(self) -> None:
        """probe_dns normalises hostname before resolution."""
        mod = _load_module("mozaiksai/core/adapters/dns_probe.py", "tests.dns_probe_norm")

        fake_infos = [(None, None, None, None, ("1.2.3.4", 0))]
        captured = {}

        def fake_getaddrinfo(host, *args, **kwargs):
            captured["host"] = host
            return fake_infos

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            asyncio.run(mod.probe_dns("  EXAMPLE.COM/  "))

        assert captured["host"] == "example.com"

    def test_deduplicates_ip_addresses(self) -> None:
        """Duplicate IPs from getaddrinfo are deduped in a_records."""
        mod = _load_module("mozaiksai/core/adapters/dns_probe.py", "tests.dns_probe_dedup")

        # Same IP returned multiple times (different socket families)
        fake_infos = [
            (None, None, None, None, ("1.2.3.4", 0)),
            (None, None, None, None, ("1.2.3.4", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            result = mod._probe_dns_sync("example.com")

        assert result.a_records.count("1.2.3.4") == 1


# ---------------------------------------------------------------------------
# TlsProbeResult shape + helpers
# ---------------------------------------------------------------------------


class TestTlsProbeResultShape:
    def test_to_dict_has_all_fields(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/tls_probe.py", "tests.tls_probe_shape")
        result = mod.TlsProbeResult(
            hostname="example.com",
            port=443,
            success=True,
            subject="CN=example.com",
            issuer="O=Let's Encrypt",
            not_before="Jan  1 00:00:00 2025 GMT",
            not_after="Apr  1 00:00:00 2025 GMT",
            days_until_expiry=90,
            expired=False,
            san_domains=["example.com", "www.example.com"],
            protocol_version="TLSv1.3",
            cipher="TLS_AES_256_GCM_SHA384",
            probe_ms=42.0,
            error=None,
        )
        d = result.to_dict()
        assert d["hostname"] == "example.com"
        assert d["success"] is True
        assert d["days_until_expiry"] == 90
        assert d["expired"] is False
        assert "example.com" in d["san_domains"]
        assert d["protocol_version"] == "TLSv1.3"
        assert d["error"] is None

    def test_expired_cert_reflected_in_result(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/tls_probe.py", "tests.tls_probe_expired")
        result = mod.TlsProbeResult(
            hostname="expired.badssl.com",
            port=443,
            success=True,
            subject="CN=expired.badssl.com",
            issuer="O=Test CA",
            not_before="Jan  1 00:00:00 2020 GMT",
            not_after="Jan  1 00:00:00 2021 GMT",
            days_until_expiry=-1000,
            expired=True,
            san_domains=["expired.badssl.com"],
            protocol_version="TLSv1.2",
            cipher=None,
            probe_ms=30.0,
            error=None,
        )
        d = result.to_dict()
        assert d["expired"] is True
        assert d["days_until_expiry"] < 0


class TestTlsCertDateParser:
    def test_parses_openssl_format(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/tls_probe.py", "tests.tls_date_parse")
        dt = mod._parse_cert_date("Apr  1 00:00:00 2025 GMT")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 4

    def test_returns_none_on_unknown_format(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/tls_probe.py", "tests.tls_date_none")
        dt = mod._parse_cert_date("not-a-date")
        assert dt is None

    def test_expired_detection_via_days_calculation(self) -> None:
        """Verify the expired/days logic used in _probe_tls_sync."""
        mod = _load_module("mozaiksai/core/adapters/tls_probe.py", "tests.tls_expiry_calc")

        # Past date → should be expired
        past = datetime.now(tz=timezone.utc) - timedelta(days=30)
        raw_past = past.strftime("%b %d %H:%M:%S %Y GMT")
        dt = mod._parse_cert_date(raw_past)
        assert dt is not None
        delta = dt - datetime.now(tz=timezone.utc)
        assert delta.days < 0  # expired

        # Future date → should not be expired
        future = datetime.now(tz=timezone.utc) + timedelta(days=90)
        raw_future = future.strftime("%b %d %H:%M:%S %Y GMT")
        dt_future = mod._parse_cert_date(raw_future)
        assert dt_future is not None
        delta_future = dt_future - datetime.now(tz=timezone.utc)
        assert delta_future.days > 0


class TestTlsProbeFailure:
    def test_connection_error_produces_failed_result(self) -> None:
        """_probe_tls_sync returns success=False on connection error."""
        mod = _load_module("mozaiksai/core/adapters/tls_probe.py", "tests.tls_probe_conn_err")

        with patch("socket.create_connection", side_effect=OSError("Connection refused")):
            result = mod._probe_tls_sync("unreachable.example.com", 443, 5.0)

        assert result.success is False
        assert result.error is not None
        assert "Connection failed" in result.error
        assert result.san_domains == []


# ---------------------------------------------------------------------------
# HttpHealthResult shape + probe_http
# ---------------------------------------------------------------------------


class TestHttpHealthResultShape:
    def test_to_dict_has_all_fields(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_shape")
        result = mod.HttpHealthResult(
            url="https://example.com/health",
            success=True,
            status_code=200,
            response_ms=123.4,
            redirected=False,
            redirect_count=0,
            final_url=None,
            content_type="application/json",
            content_length=42,
            server="nginx",
            error=None,
        )
        d = result.to_dict()
        assert d["url"] == "https://example.com/health"
        assert d["success"] is True
        assert d["status_code"] == 200
        assert d["content_type"] == "application/json"
        assert d["server"] == "nginx"
        assert d["error"] is None


class TestProbeHttp:
    def _make_mock_transport(self, *, status: int, headers: dict | None = None) -> httpx.MockTransport:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, headers=headers or {})
        return httpx.MockTransport(_handler)

    def test_200_is_success(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_200")

        transport = self._make_mock_transport(
            status=200,
            headers={"content-type": "application/json; charset=utf-8", "server": "gunicorn"},
        )

        result = asyncio.run(_patched_probe(mod, "https://example.com/health", transport))
        assert result.success is True
        assert result.status_code == 200
        assert result.content_type == "application/json"
        assert result.server == "gunicorn"
        assert result.error is None

    def test_404_is_failure(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_404")
        transport = self._make_mock_transport(status=404)
        result = asyncio.run(_patched_probe(mod, "https://example.com/missing", transport))
        assert result.success is False
        assert result.status_code == 404

    def test_500_is_failure(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_500")
        transport = self._make_mock_transport(status=500)
        result = asyncio.run(_patched_probe(mod, "https://example.com", transport))
        assert result.success is False
        assert result.status_code == 500

    def test_timeout_returns_failed_result(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_timeout")

        def _timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

        transport = httpx.MockTransport(_timeout_handler)
        result = asyncio.run(_patched_probe(mod, "https://example.com", transport))
        assert result.success is False
        assert result.status_code is None
        assert result.error is not None
        assert "Timeout" in result.error or "timed out" in result.error.lower()

    def test_network_error_returns_failed_result(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_neterr")

        def _err_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        transport = httpx.MockTransport(_err_handler)
        result = asyncio.run(_patched_probe(mod, "https://unreachable.example.com", transport))
        assert result.success is False
        assert result.error is not None

    def test_content_length_parsed_from_header(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_cl")
        transport = self._make_mock_transport(
            status=200,
            headers={"content-length": "1024"},
        )
        result = asyncio.run(_patched_probe(mod, "https://example.com", transport))
        assert result.content_length == 1024

    def test_405_falls_back_to_get(self) -> None:
        """HEAD → 405 should retry with GET and succeed."""
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_405")
        call_count = {"n": 0}

        def _mixed_handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(200, headers={"server": "apache"})

        transport = httpx.MockTransport(_mixed_handler)
        result = asyncio.run(_patched_probe(mod, "https://example.com", transport))
        assert result.success is True
        assert result.status_code == 200
        assert call_count["n"] == 2  # HEAD + GET

    def test_url_normalised_in_result(self) -> None:
        mod = _load_module("mozaiksai/core/adapters/http_health.py", "tests.http_health_url")
        transport = self._make_mock_transport(status=200)
        result = asyncio.run(_patched_probe(mod, "https://example.com/health", transport))
        assert result.url == "https://example.com/health"


# ---------------------------------------------------------------------------
# Adapter exports from __init__
# ---------------------------------------------------------------------------


def test_adapter_package_exports_all_probe_symbols() -> None:
    """All three probe adapters are declared in mozaiksai/core/adapters/__init__.py."""
    init_text = (WORKSPACE / "mozaiksai/core/adapters/__init__.py").read_text(encoding="utf-8")
    for symbol in ("DnsProbeResult", "TlsProbeResult", "HttpHealthResult", "probe_dns", "probe_tls", "probe_http"):
        assert symbol in init_text, f"Missing symbol in adapters __init__: {symbol}"


def test_check_mozaiks_adapter_exists_finds_new_adapters() -> None:
    """_check_mozaiks_adapter_exists returns True for the new probe adapters."""
    preload = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_adapter_check",
    )
    # The function looks for files matching *{provider_id}* in mozaiksai/core/adapters/
    # "dns_probe", "tls_probe", "http_health" should all resolve to True now
    assert preload._check_mozaiks_adapter_exists("dns_probe") is True
    assert preload._check_mozaiks_adapter_exists("tls_probe") is True
    assert preload._check_mozaiks_adapter_exists("http_health") is True
    # Unknown providers should still return False
    assert preload._check_mozaiks_adapter_exists("some_random_provider_xyz") is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _patched_probe(mod: Any, url: str, transport: httpx.MockTransport) -> Any:
    """Run probe_http with a mock transport injected via patched AsyncClient."""
    original = httpx.AsyncClient

    class _MockClient:
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            self._client = original(transport=transport, **kwargs)

        async def __aenter__(self):
            await self._client.__aenter__()
            return self._client

        async def __aexit__(self, *args):
            return await self._client.__aexit__(*args)

    with patch.object(mod.httpx, "AsyncClient", _MockClient):
        return await mod.probe_http(url)
