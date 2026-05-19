# ==============================================================================
# FILE: mozaiksai/core/adapters/tls_probe.py
# DESCRIPTION: Provider-neutral TLS/SSL certificate inspection probe.
#
# Uses Python stdlib `ssl` and `socket` — no external dependencies.
# Returns certificate expiry, issuer, subject, SANs, and protocol version.
#
# No external credentials required. Safe to call from any workflow tool
# or module service that needs certificate validation or expiry monitoring.
# ==============================================================================
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mozaiksai.adapters.tls_probe")

_DATE_FORMATS = ("%b %d %H:%M:%S %Y %Z", "%Y%m%d%H%M%SZ")


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TlsProbeResult:
    """Result of a TLS certificate probe."""

    __slots__ = (
        "hostname",
        "port",
        "success",
        "subject",
        "issuer",
        "not_before",
        "not_after",
        "days_until_expiry",
        "expired",
        "san_domains",
        "protocol_version",
        "cipher",
        "probe_ms",
        "error",
    )

    def __init__(
        self,
        hostname: str,
        port: int,
        success: bool,
        subject: Optional[str],
        issuer: Optional[str],
        not_before: Optional[str],
        not_after: Optional[str],
        days_until_expiry: Optional[int],
        expired: bool,
        san_domains: List[str],
        protocol_version: Optional[str],
        cipher: Optional[str],
        probe_ms: float,
        error: Optional[str],
    ) -> None:
        self.hostname = hostname
        self.port = port
        self.success = success
        self.subject = subject
        self.issuer = issuer
        self.not_before = not_before
        self.not_after = not_after
        self.days_until_expiry = days_until_expiry
        self.expired = expired
        self.san_domains = san_domains
        self.protocol_version = protocol_version
        self.cipher = cipher
        self.probe_ms = probe_ms
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hostname": self.hostname,
            "port": self.port,
            "success": self.success,
            "subject": self.subject,
            "issuer": self.issuer,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "days_until_expiry": self.days_until_expiry,
            "expired": self.expired,
            "san_domains": self.san_domains,
            "protocol_version": self.protocol_version,
            "cipher": self.cipher,
            "probe_ms": self.probe_ms,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_cert_date(raw: str) -> Optional[datetime]:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_rdns(rdns_tuple: tuple) -> str:
    """Format an RDN sequence from ssl.get_server_certificate into a string."""
    parts = []
    for rdn in rdns_tuple:
        for attr_type, attr_value in rdn:
            parts.append(f"{attr_type}={attr_value}")
    return ", ".join(parts)


def _probe_tls_sync(hostname: str, port: int, timeout: float) -> TlsProbeResult:
    start = time.monotonic()
    subject: Optional[str] = None
    issuer: Optional[str] = None
    not_before: Optional[str] = None
    not_after: Optional[str] = None
    days_until_expiry: Optional[int] = None
    expired = False
    san_domains: List[str] = []
    protocol_version: Optional[str] = None
    cipher: Optional[str] = None
    error: Optional[str] = None

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as tls_sock:
                cert = tls_sock.getpeercert()
                protocol_version = tls_sock.version()
                cipher_info = tls_sock.cipher()
                if cipher_info:
                    cipher = cipher_info[0]

        if cert:
            if "subject" in cert:
                subject = _extract_rdns(cert["subject"])
            if "issuer" in cert:
                issuer = _extract_rdns(cert["issuer"])

            raw_before = cert.get("notBefore", "")
            raw_after = cert.get("notAfter", "")
            not_before = raw_before or None
            not_after = raw_after or None

            if raw_after:
                expiry_dt = _parse_cert_date(raw_after)
                if expiry_dt:
                    now = datetime.now(tz=timezone.utc)
                    delta = expiry_dt - now
                    days_until_expiry = delta.days
                    expired = delta.days < 0

            # SANs
            for san_type, san_value in cert.get("subjectAltName", ()):
                if san_type == "DNS":
                    san_domains.append(san_value)

    except ssl.SSLCertVerificationError as exc:
        error = f"Certificate verification failed: {exc}"
    except ssl.SSLError as exc:
        error = f"TLS error: {exc}"
    except OSError as exc:
        error = f"Connection failed: {exc}"
    except Exception as exc:
        error = f"Probe error: {exc}"

    elapsed_ms = (time.monotonic() - start) * 1000
    success = error is None

    return TlsProbeResult(
        hostname=hostname,
        port=port,
        success=success,
        subject=subject,
        issuer=issuer,
        not_before=not_before,
        not_after=not_after,
        days_until_expiry=days_until_expiry,
        expired=expired,
        san_domains=san_domains,
        protocol_version=protocol_version,
        cipher=cipher,
        probe_ms=round(elapsed_ms, 2),
        error=error,
    )


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def probe_tls(
    hostname: str,
    port: int = 443,
    timeout: float = 10.0,
) -> TlsProbeResult:
    """Inspect the TLS certificate at *hostname*:*port*.

    Uses stdlib ssl — no external dependencies required.

    Args:
        hostname: Hostname to connect to (no scheme).
        port: TCP port. Defaults to 443.
        timeout: Socket connection timeout in seconds.

    Returns:
        TlsProbeResult with cert metadata, expiry, SANs, and protocol info.
    """
    hostname = hostname.strip().lower().rstrip("/")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _probe_tls_sync, hostname, port, timeout)


__all__ = ["TlsProbeResult", "probe_tls"]
