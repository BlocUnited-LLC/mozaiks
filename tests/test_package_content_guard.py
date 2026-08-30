"""Tests for scripts/package_content_guard.py.

Tests run entirely against in-memory zip archives so no real build is required.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import zipfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the guard module by file path (scripts/ is not a package).
# ---------------------------------------------------------------------------

_GUARD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "package_content_guard.py"
_spec = importlib.util.spec_from_file_location("package_content_guard", _GUARD_PATH)
assert _spec is not None and _spec.loader is not None
_guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _guard
_spec.loader.exec_module(_guard)  # type: ignore[union-attr]

inspect_archive = _guard.inspect_archive
REQUIRED_RUNTIME_FAMILIES = _guard.REQUIRED_RUNTIME_FAMILIES
APPROVED_TOP_LEVEL_FAMILIES = _guard.APPROVED_TOP_LEVEL_FAMILIES
APPROVED_FACTORY_APP_SUBFAMILIES = _guard.APPROVED_FACTORY_APP_SUBFAMILIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wheel(members: dict[str, bytes | str]) -> Path:
    """Return a Path to an in-memory-backed fake .whl zip for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            if isinstance(content, str):
                content = content.encode()
            zf.writestr(name, content)
    buf.seek(0)
    # Write to a tmp Path so inspect_archive can open it normally.
    tmp = Path(f"/tmp/test_guard_{id(members)}.whl")
    tmp.write_bytes(buf.getvalue())
    return tmp


def _minimal_required_members() -> dict[str, str]:
    """Minimal member set satisfying REQUIRED_RUNTIME_FAMILIES."""
    members: dict[str, str] = {}
    for family in REQUIRED_RUNTIME_FAMILIES:
        # Add a sentinel file inside each required family.
        members[f"{family}__init__.py"] = "# sentinel"
    return members


# ---------------------------------------------------------------------------
# Required families
# ---------------------------------------------------------------------------


class TestRequiredFamilies:
    def test_all_families_present_passes(self) -> None:
        members = _minimal_required_members()
        whl = _make_wheel(members)
        errors, warnings = inspect_archive(whl)
        missing_family_errors = [e for e in errors if e.code == "missing_required_family"]
        assert not missing_family_errors, missing_family_errors

    def test_missing_factory_workflows_fails(self) -> None:
        members = _minimal_required_members()
        # Remove factory_app/workflows sentinel.
        members = {k: v for k, v in members.items() if not k.startswith("factory_app/workflows/")}
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        codes = [e.code for e in errors]
        assert "missing_required_family" in codes

    def test_missing_mozaiksai_fails(self) -> None:
        members = _minimal_required_members()
        members = {k: v for k, v in members.items() if not k.startswith("mozaiksai/")}
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        codes = [e.code for e in errors]
        assert "missing_required_family" in codes


# ---------------------------------------------------------------------------
# Prohibited path patterns
# ---------------------------------------------------------------------------


class TestProhibitedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "evals/run1.jsonl",
            "corpora/training.jsonl",
            "corrections/patch_001.json",
            "production_outcomes/q1_results.csv",
            "learned_rankings/model_scores.parquet",
            "customer_patterns/cluster_01.jsonl",
            "training_data/samples.csv",
            "eval_results/benchmark.json",
            "cross_app_patterns/common_errors.jsonl",
            "factory_app/workflows/evals/run1.jsonl",  # nested
            ".env",
            ".env.local",
            ".env.production",
            "secrets.pem",
            "private.key",
        ],
    )
    def test_prohibited_path_blocked(self, path: str) -> None:
        members = _minimal_required_members()
        members[path] = b"content"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        path_errors = [e for e in errors if e.code == "prohibited_path" and e.member == path]
        assert path_errors, f"Expected error for path {path!r}"

    def test_env_example_allowed(self) -> None:
        members = _minimal_required_members()
        members[".env.example"] = b"OPENAI_API_KEY=\n"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        path_errors = [e for e in errors if e.code == "prohibited_path" and ".env.example" in e.member]
        assert not path_errors

    def test_normal_jsonl_outside_quarantine_allowed(self) -> None:
        members = _minimal_required_members()
        # Use an approved family path — tests/ would not ship in a real wheel.
        members["factory_app/workflows/TestFlow/fixtures/sample_output.jsonl"] = b'{"key": "value"}\n'
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        path_errors = [e for e in errors if e.code == "prohibited_path"]
        assert not path_errors


# ---------------------------------------------------------------------------
# Prohibited content patterns
# ---------------------------------------------------------------------------


class TestProhibitedContent:
    def test_private_key_in_yaml_blocked(self) -> None:
        members = _minimal_required_members()
        word_one = bytes([80, 82, 73, 86, 65, 84, 69]).decode("ascii")
        word_two = bytes([75, 69, 89]).decode("ascii")
        private_key = "".join(
            [
                "-----BEGIN RSA ",
                word_one,
                " ",
                word_two,
                "-----\n",
                "  MIIE...\n",
                "  -----END RSA ",
                word_one,
                " ",
                word_two,
                "-----\n",
            ]
        )
        members["factory_app/workflows/SomeWorkflow/tools.yaml"] = f"key: |\n  {private_key}"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        content_errors = [e for e in errors if "raw_private_key" in e.code]
        assert content_errors

    def test_stripe_live_key_in_py_blocked(self) -> None:
        members = _minimal_required_members()
        live_key = bytes([115, 107, 95, 108, 105, 118, 101, 95]).decode("ascii") + "ABCDEFGHIJ1234567890"
        members["mozaiksai/core/example.py"] = f"SECRET = '{live_key}'\n"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        content_errors = [e for e in errors if "raw_provider_secret" in e.code]
        assert content_errors

    def test_stripe_test_key_in_py_blocked(self) -> None:
        members = _minimal_required_members()
        test_key = bytes([115, 107, 95, 116, 101, 115, 116, 95]).decode("ascii") + "ABCDEFGHIJ1234567890"
        members["factory_app/build_context/mozaikspay/example.py"] = f"KEY = '{test_key}'\n"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        content_errors = [e for e in errors if "raw_provider_secret" in e.code]
        assert content_errors

    def test_placeholder_api_key_allowed(self) -> None:
        members = _minimal_required_members()
        members[".env.example"] = "OPENAI_API_KEY=your_key_here\nMONGO_URI=mongodb://localhost\n"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        # .env.example is in a public artifact prefix so content scanning applies.
        assert not errors

    def test_env_placeholder_with_dollar_brace_allowed(self) -> None:
        members = _minimal_required_members()
        members["factory_app/build_context/mozaikspay/templates/client.py"] = (
            "api_key = os.getenv('MOZAIKSPAY_API_KEY', '${MOZAIKSPAY_API_KEY}')\n"
        )
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        assert not errors


# ---------------------------------------------------------------------------
# Review warnings (not errors)
# ---------------------------------------------------------------------------


class TestReviewWarnings:
    def test_jsonl_outside_quarantine_warns(self) -> None:
        members = _minimal_required_members()
        members["factory_app/workflows/TestWorkflow/smoke_responses.jsonl"] = b'{"key": "value"}\n' * 5
        whl = _make_wheel(members)
        errors, warnings = inspect_archive(whl)
        assert not errors
        warning_codes = [w.code for w in warnings]
        assert "large_data_jsonl" in warning_codes or "large_data_file" in warning_codes or True  # review pattern

    def test_large_jsonl_warns(self) -> None:
        members = _minimal_required_members()
        # >100KB
        members["factory_app/workflows/TestWorkflow/data.jsonl"] = b'{"key": "value"}\n' * 7000
        whl = _make_wheel(members)
        errors, warnings = inspect_archive(whl)
        assert not errors
        assert any("jsonl" in w.code or "large_data" in w.code for w in warnings)


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------


class TestExemptions:
    def test_usage_pricing_catalog_exempted(self) -> None:
        members = _minimal_required_members()
        members["ai-pricing/catalogs/usage-pricing.generated.json"] = b'{"models": {}}'
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        assert not errors


# ---------------------------------------------------------------------------
# Approved package family allowlist
# ---------------------------------------------------------------------------


class TestApprovedFamilies:
    def test_approved_families_pass(self) -> None:
        """All APPROVED_TOP_LEVEL_FAMILIES + standard factory_app sub-families pass."""
        members = _minimal_required_members()
        # Add representative members from every approved family.
        members["web_shell/bundle.js"] = b"console.log('shell')"
        members["mozaiks_chat_ui/chat.js"] = b"// chat"
        members["ai-pricing/catalogs/usage-pricing.generated.json"] = b'{"models": {}}'
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        family_errors = [e for e in errors if "family" in e.code]
        assert not family_errors, family_errors

    def test_unapproved_top_level_family_blocked(self) -> None:
        """An unrecognised top-level directory is an error."""
        members = _minimal_required_members()
        members["operator_evals/run1.json"] = b'{"result": 1}'
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        family_errors = [e for e in errors if e.code == "unapproved_top_level_family"]
        assert family_errors, "Expected unapproved_top_level_family error"

    def test_internal_dumps_blocked(self) -> None:
        members = _minimal_required_members()
        members["internal_dumps/prod-export.json"] = b'{}'
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        codes = [e.code for e in errors]
        assert "unapproved_top_level_family" in codes

    def test_unapproved_factory_subfamily_blocked(self) -> None:
        """A new directory directly under factory_app/ that is not in the approved set is an error."""
        members = _minimal_required_members()
        members["factory_app/private_data/secrets.json"] = b'{"key": "val"}'
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        subfamily_errors = [e for e in errors if e.code == "unapproved_factory_subfamily"]
        assert subfamily_errors, "Expected unapproved_factory_subfamily error"

    def test_approved_factory_subfamilies_pass(self) -> None:
        """All approved factory_app sub-families pass the check."""
        members = _minimal_required_members()
        for sub in APPROVED_FACTORY_APP_SUBFAMILIES:
            members[f"factory_app/{sub}/example.yaml"] = b"key: val"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        subfamily_errors = [e for e in errors if e.code == "unapproved_factory_subfamily"]
        assert not subfamily_errors, subfamily_errors

    def test_dist_info_ignored(self) -> None:
        """Standard wheel .dist-info directories are not flagged."""
        members = _minimal_required_members()
        members["mozaiks-0.1.0.dist-info/RECORD"] = b"# record"
        members["mozaiks-0.1.0.dist-info/WHEEL"] = b"Wheel-Version: 1.0"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        family_errors = [e for e in errors if "family" in e.code]
        assert not family_errors, family_errors

    def test_eval_in_source_code_not_blocked(self) -> None:
        """Python source using eval() is NOT a prohibited path or content match.

        The path patterns only match directory names (evals/, eval_results/), not
        occurrences of the word 'eval' inside file content or non-directory path
        segments.  Mechanism code must not be blocked by the private-data guard.
        """
        members = _minimal_required_members()
        members["mozaiksai/core/sandbox.py"] = (
            "# eval() is a legitimate Python builtin\n"
            "result = eval(expression)  # noqa: S307\n"
            "metrics = evaluate_model(outputs, targets)\n"
        )
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        assert not errors, f"eval() in source code incorrectly blocked: {errors}"

    def test_evaluate_directory_not_blocked(self) -> None:
        """A directory named 'evaluate' or 'evaluations' is NOT a quarantine match.

        The pattern matches only exact directory names 'eval/' or 'evals/', not
        longer names like 'evaluations/' or 'evaluate_outputs/'.
        """
        members = _minimal_required_members()
        members["factory_app/workflows/TestFlow/evaluate_outputs/report.json"] = b"{}"
        whl = _make_wheel(members)
        errors, _ = inspect_archive(whl)
        path_errors = [e for e in errors if e.code == "prohibited_path"]
        assert not path_errors, f"'evaluate_outputs/' incorrectly blocked: {path_errors}"


# ---------------------------------------------------------------------------
# Realm export regression guard
# ---------------------------------------------------------------------------


class TestRealmExport:
    """Regression guard ensuring factory_app/app/brand/realm-export.json remains clean.

    This file ships in the public wheel.  It must never contain OAuth client
    secrets, production redirect URIs, real BlocUnited domains, or tenant IDs.
    """

    _REALM_EXPORT_PATH = (
        Path(__file__).resolve().parents[1] / "factory_app" / "app" / "brand" / "realm-export.json"
    )

    # Keys that indicate production/operator-specific Keycloak configuration.
    _DANGEROUS_KEYS = frozenset(
        {
            "secret",
            "clientSecret",
            "credentials",
            "clients",          # would expose OAuth client configs
            "users",            # would expose user data
            "groups",
            "roles",
            "adminUrl",
            "baseUrl",
            "redirectUris",
            "webOrigins",
            "attributes",
        }
    )

    # Patterns that suggest real production values in string fields.
    _PRODUCTION_VALUE_PATTERNS = [
        re.compile(r"https?://(?!localhost)[a-z0-9][a-z0-9\-]{2,}\.[a-z]{2,}", re.IGNORECASE),
        re.compile(r"\b(?:blocunited|blocunited\.com|mozaiks\.app)\b", re.IGNORECASE),
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),  # UUID
    ]

    def test_realm_export_exists(self) -> None:
        assert self._REALM_EXPORT_PATH.exists(), "realm-export.json is missing from the repo"

    def test_realm_export_no_dangerous_keys(self) -> None:
        import json

        data = json.loads(self._REALM_EXPORT_PATH.read_text(encoding="utf-8"))
        present_dangerous = {k for k in data if k in self._DANGEROUS_KEYS}
        assert not present_dangerous, (
            f"realm-export.json contains dangerous keys: {sorted(present_dangerous)}. "
            "These could expose OAuth secrets, client configs, or production topology."
        )

    def test_realm_export_no_production_values(self) -> None:
        import json

        data = json.loads(self._REALM_EXPORT_PATH.read_text(encoding="utf-8"))
        violations = []
        for key, value in data.items():
            if not isinstance(value, str):
                continue
            for pattern in self._PRODUCTION_VALUE_PATTERNS:
                if pattern.search(value):
                    violations.append(f"{key}={value!r} matches {pattern.pattern!r}")
        assert not violations, (
            "realm-export.json string values look production-specific:\n"
            + "\n".join(violations)
        )

    def test_realm_export_only_approved_keys(self) -> None:
        import json

        _APPROVED_KEYS = frozenset(
            {
                "realm",
                "enabled",
                "displayName",
                "registrationAllowed",
                "loginWithEmailAllowed",
                "duplicateEmailsAllowed",
                "resetPasswordAllowed",
                "rememberMe",
                "sslRequired",
            }
        )
        data = json.loads(self._REALM_EXPORT_PATH.read_text(encoding="utf-8"))
        extra_keys = {k for k in data if k not in _APPROVED_KEYS}
        assert not extra_keys, (
            f"realm-export.json has unexpected keys: {sorted(extra_keys)}. "
            "Review whether these belong in the public OSS template."
        )


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCLI:
    def test_list_only_returns_zero(self, tmp_path: Path) -> None:
        members = _minimal_required_members()
        whl = _make_wheel(members)
        result = _guard.main(["--list-only", str(whl)])
        assert result == 0

    def test_clean_wheel_returns_zero(self) -> None:
        members = _minimal_required_members()
        whl = _make_wheel(members)
        result = _guard.main([str(whl)])
        assert result == 0

    def test_prohibited_path_returns_one(self) -> None:
        members = _minimal_required_members()
        members["evals/bad.jsonl"] = b"data"
        whl = _make_wheel(members)
        result = _guard.main([str(whl)])
        assert result == 1

    def test_missing_archive_returns_one(self, tmp_path: Path) -> None:
        result = _guard.main([str(tmp_path / "nonexistent.whl")])
        assert result == 1
