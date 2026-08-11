"""Package content guard — inspect a built wheel or sdist for prohibited content.

Runs against the dist/ artifacts produced by ``python -m build`` BEFORE they
are uploaded to PyPI or attached to a GitHub Release.  The goal is accidental-
distribution prevention, not a substitute for thorough code review.

Usage::

    python scripts/package_content_guard.py dist/mozaiks-*.whl [dist/mozaiks-*.tar.gz ...]
    python scripts/package_content_guard.py --list-only dist/mozaiks-*.whl

Returns 0 when all checks pass, 1 when any prohibited content is detected.

Policy design
-------------
* REQUIRED_RUNTIME_FAMILIES — path prefixes that MUST appear in the wheel for
  the installed package to be functional.  Missing families are errors.
* PROHIBITED_PATH_PATTERNS — compiled regexes matched against every archive
  member path.  A match is an error that fails the run.
* PROHIBITED_CONTENT_PATTERNS — compiled regexes matched against file content
  for text files up to MAX_CONTENT_SCAN_BYTES.  A match is an error.
* REVIEW_PATH_PATTERNS — compiled regexes that flag paths for human review.
  These produce warnings only and do not fail the run.
* PATH_EXEMPTIONS — exact member paths excluded from pattern matching.
  Use sparingly; each exemption should be justified.
* LARGE_DATA_EXTENSIONS — extensions that trigger a size-based review warning
  when the member exceeds LARGE_DATA_SIZE_THRESHOLD_BYTES.

Learned-artifact policy (see OSS_PUBLICATION_POLICY.md)
---------------------------------------------------------
Directories such as evals/, corpora/, corrections/, production_outcomes/,
learned_rankings/, and customer_patterns/ are private by default.  They should
never appear in a public release artifact.  The guard enforces this at the
package level; governance_guardrails.py enforces it at the source level.
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# ---------------------------------------------------------------------------
# Policy tables
# ---------------------------------------------------------------------------

# Path prefixes that MUST appear in the wheel for Factory to function.
# Checked against the set of all member paths in the archive.
REQUIRED_RUNTIME_FAMILIES: list[str] = [
    "mozaiksai/",
    "mozaiks_cli/",
    "factory_app/workflows/",
    "factory_app/build_context/",
    "factory_app/refinement_harness/",
    "factory_app/app/",
]

# Compiled regexes matched against the archive-relative member path.
# A match is an immediate error — publication is blocked.
PROHIBITED_PATH_PATTERNS: list[re.Pattern[str]] = [
    # Learned-artifact directories — private by default per OSS_PUBLICATION_POLICY.md.
    re.compile(r"(?:^|/)evals?/", re.IGNORECASE),
    re.compile(r"(?:^|/)corpora?/", re.IGNORECASE),
    re.compile(r"(?:^|/)corrections?/", re.IGNORECASE),
    re.compile(r"(?:^|/)production_outcomes?/", re.IGNORECASE),
    re.compile(r"(?:^|/)learned_rankings?/", re.IGNORECASE),
    re.compile(r"(?:^|/)customer_patterns?/", re.IGNORECASE),
    re.compile(r"(?:^|/)training_data/", re.IGNORECASE),
    re.compile(r"(?:^|/)eval_results?/", re.IGNORECASE),
    re.compile(r"(?:^|/)cross_app_patterns?/", re.IGNORECASE),
    # .env files other than approved examples.
    re.compile(r"(?:^|/)\.env$"),
    re.compile(r"(?:^|/)\.env\.[^e]"),  # .env.local, .env.production, etc. — not .env.example
    # Private key files by extension.
    re.compile(r"\.(?:pem|p12|pfx|key)$", re.IGNORECASE),
    # Operator data exports.
    re.compile(r"(?:^|/)(?:db|database)[_-]?dump", re.IGNORECASE),
    re.compile(r"(?:^|/)(?:mongo|postgres|mysql)[_-]?dump", re.IGNORECASE),
]

# Compiled regexes matched against text content (up to MAX_CONTENT_SCAN_BYTES).
# A match is an error.
PROHIBITED_CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("raw_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "raw_provider_secret",
        re.compile(
            r"\b(?:sk_(?:live|test)|rk_live|whsec|payment_provider_(?:live|test)|stripe_(?:live|test))_[A-Za-z0-9]{10,}\b"
        ),
    ),
]

# Compiled regexes matched against member paths that produce warnings only.
REVIEW_PATH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("large_data_jsonl", re.compile(r"\.jsonl$", re.IGNORECASE)),
    ("database_file", re.compile(r"\.(?:db|sqlite3?)$", re.IGNORECASE)),
    ("parquet_file", re.compile(r"\.parquet$", re.IGNORECASE)),
]

# Member paths exempt from PROHIBITED and REVIEW pattern matching.
# Paths are matched against the normalized archive-relative member path
# (without package-level prefix such as mozaiks-0.1.0/).
PATH_EXEMPTIONS: frozenset[str] = frozenset(
    {
        # Approved AI-pricing catalog — large JSON, but not learned/private data.
        "ai-pricing/catalogs/usage-pricing.generated.json",
        "mozaiksai/core/usage/catalogs/usage-pricing.generated.json",
    }
)

# Files larger than this threshold (in bytes) with LARGE_DATA_EXTENSIONS trigger
# a review warning.
LARGE_DATA_EXTENSIONS: frozenset[str] = frozenset({".jsonl", ".csv", ".parquet"})
LARGE_DATA_SIZE_THRESHOLD_BYTES: int = 100 * 1024  # 100 KB

# Maximum bytes read from each member for content scanning.
MAX_CONTENT_SCAN_BYTES: int = 512 * 1024  # 512 KB

# ---------------------------------------------------------------------------
# Approved package family allowlist
# ---------------------------------------------------------------------------
# Any top-level directory that appears in the built wheel and is NOT in this
# set (and is not a standard wheel .dist-info / .data directory) will be
# flagged as an error.  This prevents a contributor from accidentally adding a
# new directory to MANIFEST.in / setup.py without explicit review.
#
# Extend this set ONLY after a publication-review decision (see
# OSS_PUBLICATION_POLICY.md §Publication Review Path).
APPROVED_TOP_LEVEL_FAMILIES: frozenset[str] = frozenset(
    {
        "mozaiksai",       # Python runtime package
        "mozaiks_cli",     # CLI package (includes user-facing agent_guidance/)
        "factory_app",     # Factory app bundle (sub-families reviewed separately)
        "web_shell",       # Bundled Studio frontend shell
        "mozaiks_chat_ui", # Chat UI component library
        "logs",            # Logging configuration package
        "mozaiks",         # mozaiks namespace package (if present)
        "ai-pricing",      # Approved AI pricing catalogs
    }
)

# Sub-directory names permitted directly under factory_app/.
# factory_app/XYZ/ where XYZ is not in this set is flagged — it suggests a new
# Factory data category is being shipped without an ADR publication decision.
APPROVED_FACTORY_APP_SUBFAMILIES: frozenset[str] = frozenset(
    {
        "workflows",          # Factory workflows (ADR 0002 approved)
        "build_context",      # Capability pack build contexts (ADR 0002 approved)
        "refinement_harness", # Refinement harness prompts
        "app",                # Studio first-party app bundle
    }
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentFinding:
    level: str  # "error" | "warning"
    code: str
    member: str
    message: str

    def render(self) -> str:
        return f"{self.level.upper()} [{self.code}] {self.member}: {self.message}"


# ---------------------------------------------------------------------------
# Archive iteration helpers
# ---------------------------------------------------------------------------


def _normalize_member_path(raw: str) -> str:
    """Strip leading package distribution prefix (e.g. mozaiks-0.1.0/) from sdist paths."""
    p = PurePosixPath(raw)
    parts = p.parts
    # sdist members start with <name>-<version>/ — strip it.
    if len(parts) > 1 and "-" in parts[0]:
        return str(PurePosixPath(*parts[1:]))
    return raw


def _iter_wheel(path: Path) -> list[tuple[str, int, bytes | None]]:
    """Yield (normalized_path, size_bytes, content_or_None) for each wheel member."""
    members = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            normalized = info.filename  # wheels don't have a top-level dist prefix
            size = info.file_size
            content: bytes | None = None
            suffix = PurePosixPath(info.filename).suffix.lower()
            if suffix in {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".js", ".jsx", ".ts", ".tsx", ".env", ".example", ".cfg", ".toml"} or info.filename.endswith(".env.example"):
                try:
                    content = zf.read(info.filename)[:MAX_CONTENT_SCAN_BYTES]
                except Exception:
                    pass
            members.append((normalized, size, content))
    return members


def _iter_sdist(path: Path) -> list[tuple[str, int, bytes | None]]:
    """Yield (normalized_path, size_bytes, content_or_None) for each sdist member."""
    members = []
    with tarfile.open(path, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            normalized = _normalize_member_path(member.name)
            size = member.size
            content: bytes | None = None
            suffix = PurePosixPath(normalized).suffix.lower()
            if suffix in {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".js", ".jsx", ".ts", ".tsx", ".env", ".example", ".cfg", ".toml"} or normalized.endswith(".env.example"):
                try:
                    f = tf.extractfile(member)
                    if f is not None:
                        content = f.read(MAX_CONTENT_SCAN_BYTES)
                except Exception:
                    pass
            members.append((normalized, size, content))
    return members


def _iter_archive(path: Path) -> list[tuple[str, int, bytes | None]]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        return _iter_wheel(path)
    if path.suffix in {".gz", ".bz2", ".xz"} or path.name.endswith(".tar.gz"):
        return _iter_sdist(path)
    raise ValueError(f"Unsupported archive format: {path.name}")


# ---------------------------------------------------------------------------
# Inspection logic
# ---------------------------------------------------------------------------


def _is_exempt(normalized: str) -> bool:
    return normalized in PATH_EXEMPTIONS


def inspect_archive(path: Path) -> tuple[list[ContentFinding], list[ContentFinding]]:
    """Return (errors, warnings) for the given wheel or sdist."""
    errors: list[ContentFinding] = []
    warnings: list[ContentFinding] = []

    members = _iter_archive(path)
    member_paths = {m[0] for m in members}

    # 1. Required runtime families check.
    for family in REQUIRED_RUNTIME_FAMILIES:
        if not any(p.startswith(family) for p in member_paths):
            errors.append(
                ContentFinding(
                    level="error",
                    code="missing_required_family",
                    member=f"<archive:{path.name}>",
                    message=f"required runtime family '{family}' not found in package — wheel may be incomplete",
                )
            )

    # 2. Approved family allowlist check.
    #    Detects new top-level or factory_app sub-families shipping without review.
    seen_top_levels: set[str] = set()
    seen_factory_subfamilies: set[str] = set()
    for path in member_paths:
        parts = PurePosixPath(path).parts
        # Root-level files (single-component paths, e.g. .env.example, README.md)
        # are not part of a named "family" — skip the directory allowlist check.
        # Their content/path is still checked by the prohibited-pattern rules above.
        if len(parts) < 2:
            continue
        top = parts[0]
        # Standard wheel metadata directories are always allowed.
        if top.endswith(".dist-info") or top.endswith(".data"):
            continue
        seen_top_levels.add(top)
        if top == "factory_app" and len(parts) > 1:
            sub = parts[1]
            # Python packaging artifacts (__init__.py, __pycache__) are always OK.
            if not sub.startswith("__"):
                seen_factory_subfamilies.add(sub)

    for family in sorted(seen_top_levels):
        if family not in APPROVED_TOP_LEVEL_FAMILIES:
            errors.append(
                ContentFinding(
                    level="error",
                    code="unapproved_top_level_family",
                    member=f"{family}/",
                    message=(
                        f"top-level package family '{family}' is not in APPROVED_TOP_LEVEL_FAMILIES "
                        "— update the allowlist after a publication-review decision"
                    ),
                )
            )

    for subfamily in sorted(seen_factory_subfamilies):
        if subfamily not in APPROVED_FACTORY_APP_SUBFAMILIES:
            errors.append(
                ContentFinding(
                    level="error",
                    code="unapproved_factory_subfamily",
                    member=f"factory_app/{subfamily}/",
                    message=(
                        f"factory_app sub-family '{subfamily}' is not in APPROVED_FACTORY_APP_SUBFAMILIES "
                        "— this content may require an ADR publication-review decision"
                    ),
                )
            )

    # 3. Per-member checks.
    for normalized, size, content in members:
        if _is_exempt(normalized):
            continue

        # Prohibited path patterns.
        for pattern in PROHIBITED_PATH_PATTERNS:
            if pattern.search(normalized):
                errors.append(
                    ContentFinding(
                        level="error",
                        code="prohibited_path",
                        member=normalized,
                        message=f"path matches prohibited pattern '{pattern.pattern}' — must not ship in public release",
                    )
                )
                break  # one error per member is enough

        # Review path patterns.
        for code, pattern in REVIEW_PATH_PATTERNS:
            if pattern.search(normalized):
                warnings.append(
                    ContentFinding(
                        level="warning",
                        code=code,
                        member=normalized,
                        message="path matches review pattern — verify this file should be in the public package",
                    )
                )
                break

        # Large data file check.
        ext = PurePosixPath(normalized).suffix.lower()
        if ext in LARGE_DATA_EXTENSIONS and size > LARGE_DATA_SIZE_THRESHOLD_BYTES:
            warnings.append(
                ContentFinding(
                    level="warning",
                    code="large_data_file",
                    member=normalized,
                    message=f"file is {size // 1024}KB — verify it is not a training/eval corpus",
                )
            )

        # Prohibited content patterns (text files only).
        if content is not None:
            try:
                text = content.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            for code, pattern in PROHIBITED_CONTENT_PATTERNS:
                if pattern.search(text):
                    errors.append(
                        ContentFinding(
                            level="error",
                            code=f"prohibited_content:{code}",
                            member=normalized,
                            message=f"file content matches '{code}' pattern — must not ship in public release",
                        )
                    )

    return errors, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect built wheels/sdists for prohibited content before publication."
    )
    parser.add_argument("archives", nargs="+", type=Path, help="wheel or sdist file(s) to inspect")
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list all member paths without running policy checks",
    )
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="treat review warnings as errors (exit 1 when any warning is found)",
    )
    args = parser.parse_args(argv)

    overall_errors = 0
    overall_warnings = 0

    for archive_path in args.archives:
        if not archive_path.exists():
            print(f"ERROR: archive not found: {archive_path}", file=sys.stderr)
            overall_errors += 1
            continue

        print(f"\n=== {archive_path.name} ===")

        if args.list_only:
            members = _iter_archive(archive_path)
            for normalized, size, _ in sorted(members, key=lambda m: m[0]):
                print(f"  {size:>10,}  {normalized}")
            print(f"\n  {len(members)} members total")
            continue

        errors, warnings = inspect_archive(archive_path)
        overall_errors += len(errors)
        overall_warnings += len(warnings)

        for finding in errors:
            print(finding.render(), file=sys.stderr)
        for finding in warnings:
            print(finding.render())

        if not errors and not warnings:
            print(f"  OK — {archive_path.name} passed all content checks.")
        elif not errors:
            print(f"  {len(warnings)} review warning(s) — no errors.")
        else:
            print(f"  {len(errors)} error(s), {len(warnings)} warning(s).", file=sys.stderr)

    if args.list_only:
        return 0

    print()
    if overall_errors:
        print(
            f"Package content guard FAILED: {overall_errors} error(s) across {len(args.archives)} archive(s).",
            file=sys.stderr,
        )
        return 1

    if overall_warnings and args.warnings_as_errors:
        print(
            f"Package content guard FAILED (--warnings-as-errors): {overall_warnings} warning(s).",
            file=sys.stderr,
        )
        return 1

    print(
        f"Package content guard PASSED: {overall_warnings} review warning(s), 0 errors."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
