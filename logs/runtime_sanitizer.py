"""
Runtime log sanitizer for AG2 (AutoGen) runtime file logger output.

Purpose:
- Redact secrets (API keys, bearer tokens, connection strings) from runtime.log.
- Optionally sanitize in place or write to a separate .sanitized.log file.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Iterable


_SECRET_PATTERNS: Iterable[tuple[re.Pattern[str], str]] = (
    # OpenAI keys
    (
        re.compile(r"sk-[A-Za-z0-9]{10,}"),
        "sk-***REDACTED***",
    ),
    # openai_api_key variations
    (
        re.compile(r"(openai[_-]?api[_-]?key\"?\s*[:=]\s*\")[^\"\s]+(\")", re.IGNORECASE),
        r"\1***REDACTED***\2",
    ),
    # generic api_key with equals (api_key=...)
    (
        re.compile(r"(api[_-]?key\s*=\s*)[^\s;]+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # Generic api_key fields in JSON-like blobs: api_key":"..."
    (
        re.compile(r"(api_key\"?\s*:\s*\")[^\"]+(\")", re.IGNORECASE),
        r"\1***REDACTED***\2",
    ),
    # Bearer tokens
    (
        re.compile(r"(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._-]+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # Mongo connection credentials in URIs
    (
        re.compile(r"(mongodb(?:\+srv)?://[^:/@]+:)([^@]+)(@)", re.IGNORECASE),
        r"\1***REDACTED***\3",
    ),
    # Azure Storage AccountKey
    (
        re.compile(r"(AccountKey=)[^;\s]+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
)


def _sanitize_line(line: str) -> str:
    for pat, repl in _SECRET_PATTERNS:
        try:
            line = pat.sub(repl, line)
        except Exception:
            # best-effort; continue on regex issues
            pass
    return line


def sanitize_runtime_log_file(path: str | Path, *, in_place: bool = True) -> Path:
    """
    Redact secrets from the given runtime log file.

    Args:
        path: Path to the runtime.log file.
        in_place: If True, overwrite the original file; else write alongside with .sanitized suffix.

    Returns:
        Path to the sanitized log file (same as input when in_place=True).
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return p

    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        # Fallback to binary read then decode best-effort
        data = p.read_bytes()
        text = data.decode("utf-8", errors="ignore")

    sanitized = "\n".join(_sanitize_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")

    if in_place:
        p.write_text(sanitized, encoding="utf-8", errors="ignore")
        return p
    else:
        out = p.with_suffix(p.suffix + ".sanitized")
        out.write_text(sanitized, encoding="utf-8", errors="ignore")
        return out
