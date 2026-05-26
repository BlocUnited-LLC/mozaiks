"""generated_bundle_scanner — scan a generated app bundle for forbidden patterns.

Checks that the generated app does not:

- Directly import or call the Stripe SDK.
  Generated apps must use the hosted MozaiksPay adapter
  (backend/integrations/mozaikspay_client.py), not Stripe directly.

- Reference the Stripe Refunds API (/v1/refunds) in any file.
  Refund execution is hosted-platform-only; generated apps must not
  mutate refunds via the Stripe Refunds API.

- Embed raw Stripe secret key literals (sk_live_* / sk_test_*).
  Credentials are managed by the hosted platform only.

Called by generate_and_download.py after the full files_map is assembled.
Returns a list of human-readable error strings. An empty list means clean.
"""
from __future__ import annotations

import re
from typing import Dict, List

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Stripe SDK: direct top-level import in Python files.
_STRIPE_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+stripe\b|from\s+stripe\b)",
    re.MULTILINE,
)

# Stripe SDK: api_key assignment anywhere in Python files.
_STRIPE_API_KEY_RE = re.compile(r"\bstripe\.api_key\s*=")

# Stripe Refunds SDK: Python SDK refund creation calls.
_STRIPE_REFUND_CREATE_RE = re.compile(
    r"\bstripe\.Refund\.create\b|\bstripe\.refunds\.create\b"
)

# Stripe Refunds API: direct endpoint string literal in any scannable file.
_STRIPE_REFUND_ENDPOINT_RE = re.compile(r"""['"]/v1/refunds['"]""")

# Stripe secret key literal in any scannable file.
# Matches sk_live_* and sk_test_* with at least 10 trailing alphanum chars.
_STRIPE_SECRET_LITERAL_RE = re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{10,}")

# File suffixes and compound endings that carry executable or config content.
# Checked via str.endswith so compound extensions like .env.example work.
_SCANNABLE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".yaml", ".yml", ".env.example", ".env",
)


def _is_scannable(path: str) -> bool:
    """Return True if this file path should be scanned for forbidden patterns."""
    lpath = path.lower()
    return any(lpath.endswith(s) for s in _SCANNABLE_SUFFIXES)


def _is_python(path: str) -> bool:
    return path.lower().endswith(".py")


def scan_generated_bundle(files_map: Dict[str, str]) -> List[str]:
    """Scan files_map for forbidden patterns.

    Returns a list of human-readable error strings.
    An empty list means the bundle is clean and safe to deliver.

    Checks applied per file type:
    - All scannable files: Stripe secret key literal, Stripe /v1/refunds endpoint.
    - Python files only: Stripe SDK import, stripe.api_key assignment,
      stripe.Refund.create / stripe.refunds.create call.
    """
    errors: List[str] = []

    for path, content in files_map.items():
        if not isinstance(path, str) or not isinstance(content, str):
            continue

        if not _is_scannable(path):
            continue

        is_py = _is_python(path)

        # ---- checks that apply to all scannable file types ----

        if _STRIPE_SECRET_LITERAL_RE.search(content):
            errors.append(
                f"{path}: contains a Stripe secret key literal "
                "(sk_live_* or sk_test_*). Generated apps must not embed "
                "Stripe credentials. Credentials are managed by the hosted "
                "platform only."
            )

        if _STRIPE_REFUND_ENDPOINT_RE.search(content):
            errors.append(
                f"{path}: references the Stripe Refunds API endpoint "
                "('/v1/refunds'). Refund execution is hosted-platform-only; "
                "generated apps must not call the Stripe Refunds API directly."
            )

        if not is_py:
            continue

        # ---- Python-only checks ----

        if _STRIPE_IMPORT_RE.search(content):
            errors.append(
                f"{path}: imports the Stripe SDK directly "
                "('import stripe' or 'from stripe ...'). Generated apps must "
                "call payment services via the hosted MozaiksPay adapter "
                "(backend/integrations/mozaikspay_client.py), not Stripe."
            )

        if _STRIPE_API_KEY_RE.search(content):
            errors.append(
                f"{path}: assigns stripe.api_key. Stripe credentials are "
                "managed by the hosted platform, not generated apps."
            )

        if _STRIPE_REFUND_CREATE_RE.search(content):
            errors.append(
                f"{path}: calls stripe.Refund.create or stripe.refunds.create. "
                "Refund execution is hosted-platform-only. Generated apps must "
                "not mutate refunds via the Stripe SDK."
            )

    return errors


__all__ = ["scan_generated_bundle"]
