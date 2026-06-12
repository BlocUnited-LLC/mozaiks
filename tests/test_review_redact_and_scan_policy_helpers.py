"""
Pure helper unit tests for:
  mozaiksai/control_plane/review.py: redact_review_notes
  mozaiksai/core/app_context/scan_policy.py: _is_sensitive_relpath

Covers (review.py):
  redact_review_notes:
    - None → None
    - string with no secret patterns → unchanged
    - "api_key=abc123" → "api_key=<redacted>"
    - "token=xyz" → "token=<redacted>"
    - "password=hunter2" → "password=<redacted>"
    - "secret=val" → "secret=<redacted>"
    - "credential=val" → "credential=<redacted>"
    - "private_key=val" → "private_key=<redacted>"
    - "api-key=val" → redacted (hyphen form)
    - "TOKEN=val" (uppercase) → redacted (case insensitive)
    - multiple occurrences redacted
    - surrounding text preserved
    - "api_key: value" (colon separator) → redacted

Covers (scan_policy.py):
  _is_sensitive_relpath:
    - ".env" exact name → True
    - ".npmrc" exact name → True
    - "app/.env" → True (exact name in path)
    - "secrets/api.yaml" → True (sensitive dir name)
    - ".ssh/id_rsa" → True
    - "credentials/config.yaml" → True
    - "vault/keys.json" → True
    - "server.pem" → True (sensitive suffix)
    - "cert.p12" → True
    - "keystore.pfx" → True
    - "private_key.txt" → True (sensitive name fragment)
    - "credential_store.json" → True (fragment)
    - "secret_config.yaml" → True (fragment)
    - "modules/billing/handler.py" → False
    - "app/config/settings.yaml" → False
    - "ui/page.yaml" → False
"""
from __future__ import annotations

from pathlib import PurePosixPath

from mozaiksai.control_plane.review import redact_review_notes
from mozaiksai.core.app_context.scan_policy import _is_sensitive_relpath

# ---------------------------------------------------------------------------
# 1. redact_review_notes
# ---------------------------------------------------------------------------

class TestRedactReviewNotes:
    def test_none_returns_none(self):
        assert redact_review_notes(None) is None

    def test_clean_text_unchanged(self):
        result = redact_review_notes("The changes look good.")
        assert result == "The changes look good."

    def test_api_key_equals_redacted(self):
        result = redact_review_notes("api_key=abc123def456")
        assert "<redacted>" in result
        assert "abc123def456" not in result

    def test_token_redacted(self):
        result = redact_review_notes("token=xyz789")
        assert "<redacted>" in result
        assert "xyz789" not in result

    def test_password_redacted(self):
        result = redact_review_notes("password=hunter2")
        assert "<redacted>" in result
        assert "hunter2" not in result

    def test_secret_redacted(self):
        result = redact_review_notes("secret=mysecretvalue")
        assert "<redacted>" in result
        assert "mysecretvalue" not in result

    def test_credential_redacted(self):
        result = redact_review_notes("credential=my_cred")
        assert "<redacted>" in result

    def test_private_key_hyphen_form(self):
        result = redact_review_notes("private-key=abc123")
        assert "<redacted>" in result
        assert "abc123" not in result

    def test_private_key_underscore_form(self):
        result = redact_review_notes("private_key=abc123")
        assert "<redacted>" in result

    def test_case_insensitive_token(self):
        result = redact_review_notes("TOKEN=xyz789")
        assert "<redacted>" in result
        assert "xyz789" not in result

    def test_colon_separator_redacted(self):
        result = redact_review_notes("api_key: abc123")
        assert "<redacted>" in result
        assert "abc123" not in result

    def test_surrounding_text_preserved(self):
        result = redact_review_notes("Looks good. token=xyz789. Everything else is fine.")
        assert "Looks good." in result
        assert "Everything else is fine." in result
        assert "xyz789" not in result

    def test_multiple_occurrences_all_redacted(self):
        text = "token=abc123 and password=xyz789"
        result = redact_review_notes(text)
        assert "abc123" not in result
        assert "xyz789" not in result

    def test_api_key_no_separator_not_redacted(self):
        # Pattern requires = or : separator after whitespace
        result = redact_review_notes("check the api_key field")
        # Should not redact "field" since there's no = or :
        assert "field" in result

    def test_empty_string_unchanged(self):
        result = redact_review_notes("")
        assert result == ""


# ---------------------------------------------------------------------------
# 2. _is_sensitive_relpath
# ---------------------------------------------------------------------------

class TestIsSensitiveRelpath:
    def test_env_file_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath(".env")) is True

    def test_env_local_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath(".env.local")) is True

    def test_npmrc_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath(".npmrc")) is True

    def test_pypirc_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath(".pypirc")) is True

    def test_env_in_subpath_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("app/.env")) is True

    def test_secrets_dir_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("secrets/api.yaml")) is True

    def test_ssh_dir_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath(".ssh/id_rsa")) is True

    def test_credentials_dir_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("credentials/config.yaml")) is True

    def test_vault_dir_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("vault/keys.json")) is True

    def test_pem_suffix_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("server.pem")) is True

    def test_p12_suffix_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("cert.p12")) is True

    def test_pfx_suffix_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("keystore.pfx")) is True

    def test_key_suffix_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("id_rsa.key")) is True

    def test_private_key_fragment_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("private_key.txt")) is True

    def test_credential_fragment_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("credential_store.json")) is True

    def test_secret_fragment_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("secret_config.yaml")) is True

    def test_normal_handler_not_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("modules/billing/handler.py")) is False

    def test_normal_config_not_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("app/config/settings.yaml")) is False

    def test_ui_page_not_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("ui/page.yaml")) is False

    def test_plain_python_file_not_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("service.py")) is False

    def test_readme_not_sensitive(self):
        assert _is_sensitive_relpath(PurePosixPath("README.md")) is False
