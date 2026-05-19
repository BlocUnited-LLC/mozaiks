# ==============================================================================
# FILE: mozaiksai/core/adapters/dns_probe.py
# DESCRIPTION: Provider-neutral DNS resolution probe.
#
# Uses Python stdlib `socket` for A/AAAA records (always available).
# Uses `dnspython` for MX, NS, CNAME, TXT records when installed (optional).
#
# No external credentials required. Safe to call from any workflow tool
# or module service that needs hostname resolution or DNS record inspection.
# ==============================================================================
from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mozaiksai.adapters.dns_probe")

# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class DnsProbeResult:
    """Result of a DNS probe operation."""

    __slots__ = (
        "hostname",
        "success",
        "a_records",
        "aaaa_records",
        "mx_records",
        "ns_records",
        "cname",
        "txt_records",
        "resolution_ms",
        "error",
    )

    def __init__(
        self,
        hostname: str,
        success: bool,
        a_records: List[str],
        aaaa_records: List[str],
        mx_records: List[str],
        ns_records: List[str],
        cname: Optional[str],
        txt_records: List[str],
        resolution_ms: float,
        error: Optional[str],
    ) -> None:
        self.hostname = hostname
        self.success = success
        self.a_records = a_records
        self.aaaa_records = aaaa_records
        self.mx_records = mx_records
        self.ns_records = ns_records
        self.cname = cname
        self.txt_records = txt_records
        self.resolution_ms = resolution_ms
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hostname": self.hostname,
            "success": self.success,
            "a_records": self.a_records,
            "aaaa_records": self.aaaa_records,
            "mx_records": self.mx_records,
            "ns_records": self.ns_records,
            "cname": self.cname,
            "txt_records": self.txt_records,
            "resolution_ms": self.resolution_ms,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Optional dnspython support
# ---------------------------------------------------------------------------


def _try_import_dnspython() -> Any:
    try:
        import dns.resolver  # type: ignore[import-untyped]
        return dns.resolver
    except ImportError:
        return None


def _query_dnspython(
    resolver: Any,
    hostname: str,
    record_type: str,
) -> List[str]:
    """Query a DNS record type using dnspython. Returns empty list on failure."""
    try:
        answers = resolver.resolve(hostname, record_type, lifetime=8.0)
        return [str(r) for r in answers]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core probe logic (sync, run in executor for async callers)
# ---------------------------------------------------------------------------


def _probe_dns_sync(hostname: str) -> DnsProbeResult:
    start = time.monotonic()
    a_records: List[str] = []
    aaaa_records: List[str] = []
    mx_records: List[str] = []
    ns_records: List[str] = []
    cname: Optional[str] = None
    txt_records: List[str] = []
    error: Optional[str] = None

    # A / AAAA — always via stdlib
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            addr = info[4][0]
            if ":" in addr:
                if addr not in aaaa_records:
                    aaaa_records.append(addr)
            else:
                if addr not in a_records:
                    a_records.append(addr)
    except socket.gaierror as exc:
        error = f"DNS resolution failed: {exc}"

    # MX / NS / CNAME / TXT — via dnspython when available
    resolver = _try_import_dnspython()
    if resolver is not None:
        mx_raw = _query_dnspython(resolver, hostname, "MX")
        # MX records look like "10 mail.example.com." — strip priority prefix
        mx_records = [r.split(None, 1)[-1].rstrip(".") if " " in r else r.rstrip(".") for r in mx_raw]

        ns_raw = _query_dnspython(resolver, hostname, "NS")
        ns_records = [r.rstrip(".") for r in ns_raw]

        cname_raw = _query_dnspython(resolver, hostname, "CNAME")
        cname = cname_raw[0].rstrip(".") if cname_raw else None

        txt_records = _query_dnspython(resolver, hostname, "TXT")

    elapsed_ms = (time.monotonic() - start) * 1000
    success = bool(a_records or aaaa_records) and error is None

    return DnsProbeResult(
        hostname=hostname,
        success=success,
        a_records=a_records,
        aaaa_records=aaaa_records,
        mx_records=mx_records,
        ns_records=ns_records,
        cname=cname,
        txt_records=txt_records,
        resolution_ms=round(elapsed_ms, 2),
        error=error,
    )


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def probe_dns(hostname: str) -> DnsProbeResult:
    """Resolve DNS records for *hostname*.

    Always resolves A/AAAA via stdlib. Resolves MX, NS, CNAME, TXT when
    dnspython is installed.

    Args:
        hostname: The hostname to resolve (no scheme, no path).

    Returns:
        DnsProbeResult with record lists and timing.
    """
    hostname = hostname.strip().lower().rstrip("/")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _probe_dns_sync, hostname)


__all__ = ["DnsProbeResult", "probe_dns"]
