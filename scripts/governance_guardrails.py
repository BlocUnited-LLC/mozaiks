"""Lightweight OSS-boundary guardrails for public Mozaiks changes."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".env",
    ".example",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}

EXCLUDED_DIRS = {
    ".git",
    ".local",
    ".mkdocs-tmp",
    ".mypy_cache",
    ".pytest_cache",
    ".release-local-venv",
    ".release-venv",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site",
    "venv",
}

EXCLUDED_PREFIXES = {
    ".claude/worktrees",
}

GRANTED_PERMISSIONS_NONE_ALLOWLIST = {
    "ARCHITECTURAL_INVARIANTS.md",
    "CHANGELOG.md",
    "docs/architecture/app/tenant-auth-and-scope.md",
    "docs/guides/adding-modules/01-overview.md",
    "mozaiksai/core/runtime/composition/module_dispatch.py",
    "mozaiksai/hosts/routers/modules.py",
    "scripts/governance_guardrails.py",
    "tests/test_governance_guardrails.py",
    "tests/test_module_action_dispatch_public_api.py",
    "tests/test_module_executor_dispatch.py",
    "tests/test_module_executor_permission_enforcement.py",
    "tests/test_module_loader_contracts.py",
}

PUBLIC_ARTIFACT_PREFIXES = (
    "factory_app/build_context/",
    "factory_app/workflows/",
    "factory_app/refinement_harness/",
    "generated/",
    "docs/",
    ".github/",
)

REVIEW_SURFACE_PREFIXES = (
    "factory_app/workflows/",
    "factory_app/refinement_harness/",
    "factory_app/build_context/",
    "mozaiksai/control_plane/",
)

REVIEW_SURFACE_FILENAMES = {
    "agents.yaml",
    "orchestrator.yaml",
    "structured_outputs.yaml",
    "tools.yaml",
    "context.yaml",
    "harness.yaml",
    "policies.yaml",
}

REVIEW_MARKERS = (
    "oss_publication:",
    "publication_review:",
    "governance_review:",
)

GRANTED_PERMISSIONS_NONE_RE = re.compile(r"\bgranted_permissions\s*=\s*None\b")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
PROVIDER_SECRET_RE = re.compile(
    r"\b(?:sk_live|rk_live|whsec|payment_provider_(?:live|test)|stripe_(?:live|test))_[A-Za-z0-9]{10,}\b"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|client[_-]?secret|private[_-]?key|webhook[_-]?secret|password|connection[_-]?string)"
    r"\s*[:=]\s*['\"]?(?!\$\{|<|your_|example|sample|changeme|redacted|env:|secret-name|secret_ref)"
    r"[A-Za-z0-9_./+=:-]{18,}"
)
PROVIDER_MUTATION_PATH_RE = re.compile(
    r"(?i)(services/adapters/(?:deployment|dns|registrar|payments|cloud|ci|ssl)|providers/.+(?:azure|cloudflare|stripe|godaddy|opensrs))"
)
PROVIDER_MUTATION_CODE_RE = re.compile(
    r"(?i)\b(?:create|delete|deploy|provision|register_domain|charge|refund|transfer|set_secret|update_dns)\b"
)
PUBLIC_SCHEMA_PATH_RE = re.compile(
    r"(?i)(?:^|/)(?:schemas?|contracts?|protocols?|manifests?|registry)(?:/|[^/]*\.(?:ya?ml|json)$)"
)
SCHEMA_VERSION_RE = re.compile(
    r"(?im)(?:^|\s)(?:schema_version|contract_version|protocol_version|version|\$schema|kind)\s*[:=]"
)


@dataclass(frozen=True)
class GovernanceFinding:
    severity: str
    code: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.severity.upper()} {self.code} {location}: {self.message}"


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _excluded(path: Path, repo_root: Path) -> bool:
    relative = _relative(path, repo_root)
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return True
    return any(relative == prefix or relative.startswith(f"{prefix}/") for prefix in EXCLUDED_PREFIXES)


def _text_file(path: Path) -> bool:
    return path.name == ".env.example" or path.suffix in TEXT_SUFFIXES


def _iter_all_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if _excluded(path, repo_root):
            continue
        if path.is_file() and _text_file(path):
            files.append(path)
    return files


def _git_files(args: list[str], repo_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [
        repo_root / line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _iter_changed_files(repo_root: Path) -> list[Path]:
    changed = _git_files(["diff", "--name-only", "--diff-filter=ACMRT", "HEAD"], repo_root)
    untracked = _git_files(["ls-files", "--others", "--exclude-standard"], repo_root)
    paths = [path for path in [*changed, *untracked] if path.exists()]
    return [path for path in paths if not _excluded(path, repo_root) and path.is_file() and _text_file(path)]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _line_for(text: str, match: re.Match[str]) -> int:
    return text.count("\n", 0, match.start()) + 1


def _is_public_artifact(relative: str) -> bool:
    return relative.startswith(PUBLIC_ARTIFACT_PREFIXES)


def _is_review_surface(relative: str) -> bool:
    return relative.startswith(REVIEW_SURFACE_PREFIXES) and (
        Path(relative).name in REVIEW_SURFACE_FILENAMES
        or "/prompts/" in relative
        or "/tools/" in relative
    )


def _has_review_marker(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in REVIEW_MARKERS)


def _scan_file(path: Path, repo_root: Path) -> tuple[list[GovernanceFinding], list[GovernanceFinding]]:
    text = _read_text(path)
    if text is None:
        return [], []

    relative = _relative(path, repo_root)
    errors: list[GovernanceFinding] = []
    notices: list[GovernanceFinding] = []

    if GRANTED_PERMISSIONS_NONE_RE.search(text) and relative not in GRANTED_PERMISSIONS_NONE_ALLOWLIST:
        match = GRANTED_PERMISSIONS_NONE_RE.search(text)
        assert match is not None
        errors.append(
            GovernanceFinding(
                severity="error",
                code="authority_bypass_not_reviewed",
                path=relative,
                line=_line_for(text, match),
                message="new granted_permissions=None usage requires explicit authority review",
            )
        )

    if _is_public_artifact(relative):
        for pattern, code in (
            (PRIVATE_KEY_RE, "raw_private_key"),
            (PROVIDER_SECRET_RE, "raw_provider_secret"),
        ):
            match = pattern.search(text)
            if match:
                errors.append(
                    GovernanceFinding(
                        severity="error",
                        code=code,
                        path=relative,
                        line=_line_for(text, match),
                        message="public or generated artifacts must carry secret references, not raw values",
                    )
                )
                break
        if path.name == ".env.example" or path.suffix in {".env", ".example", ".yaml", ".yml", ".json", ".md"}:
            match = SECRET_ASSIGNMENT_RE.search(text)
            if match:
                errors.append(
                    GovernanceFinding(
                        severity="error",
                        code="raw_secret_assignment",
                        path=relative,
                        line=_line_for(text, match),
                        message="public or generated artifacts must carry secret references, not raw values",
                    )
                )

    if PROVIDER_MUTATION_PATH_RE.search(relative) and PROVIDER_MUTATION_CODE_RE.search(text) and not _has_review_marker(text):
        match = PROVIDER_MUTATION_CODE_RE.search(text)
        assert match is not None
        errors.append(
            GovernanceFinding(
                severity="error",
                code="provider_mutation_review_missing",
                path=relative,
                line=_line_for(text, match),
                message="provider-specific production mutation code requires publication review",
            )
        )

    if path.suffix in {".yaml", ".yml", ".json"} and PUBLIC_SCHEMA_PATH_RE.search(relative):
        if not SCHEMA_VERSION_RE.search(text):
            notices.append(
                GovernanceFinding(
                    severity="notice",
                    code="public_schema_needs_classification",
                    path=relative,
                    line=1,
                    message="public schema or contract files should expose versioning or classification",
                )
            )

    if _is_review_surface(relative) and not _has_review_marker(text):
        notices.append(
            GovernanceFinding(
                severity="notice",
                code="publication_review_surface",
                path=relative,
                line=1,
                message="workflow, prompt, build-context, or intelligence surface should be classified in PR review",
            )
        )

    return errors, notices


def scan_paths(paths: list[Path], repo_root: Path = REPO_ROOT) -> tuple[list[GovernanceFinding], list[GovernanceFinding]]:
    errors: list[GovernanceFinding] = []
    notices: list[GovernanceFinding] = []
    for path in paths:
        if _excluded(path, repo_root) or not path.is_file() or not _text_file(path):
            continue
        file_errors, file_notices = _scan_file(path, repo_root)
        errors.extend(file_errors)
        notices.extend(file_notices)
    return errors, notices


def run_scan(*, all_files: bool = False, paths: list[str] | None = None, repo_root: Path = REPO_ROOT) -> tuple[list[GovernanceFinding], list[GovernanceFinding]]:
    if paths:
        scan_targets = [repo_root / path for path in paths]
    elif all_files:
        scan_targets = _iter_all_files(repo_root)
    else:
        scan_targets = _iter_changed_files(repo_root)
    return scan_paths(scan_targets, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="scan all repository text files")
    parser.add_argument("--errors-only", action="store_true", help="hide non-blocking review notices")
    parser.add_argument("--paths", nargs="*", help="specific repo-relative paths to scan")
    args = parser.parse_args(argv)

    errors, notices = run_scan(all_files=args.all, paths=args.paths)

    for finding in errors:
        print(finding.render(), file=sys.stderr)
    if not args.errors_only:
        for finding in notices[:50]:
            print(finding.render())
        if len(notices) > 50:
            print(f"NOTICE governance_notice_limit: {len(notices) - 50} additional review notices omitted")

    if errors:
        print(f"Governance guardrails failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    if args.errors_only:
        print("Governance guardrails passed.")
    else:
        print(f"Governance guardrails passed with {len(notices)} notice(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
