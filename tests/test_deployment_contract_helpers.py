"""
deployment_contract.py pure helper unit tests.

Covers:
  _bool:
    - bool True/False → returned as-is
    - string "true"/"yes"/"1" → True
    - string "false"/"no"/"0" → False
    - case-insensitive string → True
    - default=False for non-string/bool

  _list_of_str:
    - non-list → []
    - list of strings → stripped, empty excluded
    - non-string items in list → excluded
    - empty list → []

  _looks_like_url:
    - https:// prefix → True
    - http:// prefix → True
    - no prefix → False
    - empty/None → False
    - URL with spaces → False

  _forbidden_secret_key:
    - key containing "secret" → True
    - key containing "token" → True
    - key containing "password" → True
    - key containing "credential" → True
    - key containing "authorization" → True
    - benign key "app_name" → False
    - case-insensitive → True

  _forbidden_secret_value:
    - value starting with "-----BEGIN" → True
    - value starting with "ghp_" → True
    - value starting with "github_pat_" → True
    - value starting with "sk-" → True
    - value starting with "xoxb-" → True
    - value starting with "xoxp-" → True
    - env:// ref → False (allowed reference)
    - vault:// ref → False (allowed reference)
    - ${VAR} template → False (allowed variable reference)
    - empty/None → False
    - benign value → False

  _first_forbidden_secret_path:
    - empty payload → None
    - dict with forbidden key → key path
    - dict with forbidden value → key path
    - nested forbidden key → dotted path
    - list with forbidden value → indexed path
    - deeply nested → correct path
    - no forbidden content → None

  _workflow_secret_refs:
    - no references → empty set
    - one ${{ secrets.NAME }} → {"NAME"}
    - multiple references → all returned
    - lowercase secret name → not matched (requires uppercase)
    - spaces around secrets.NAME → matched

  _workflow_input_refs:
    - no references → empty set
    - one ${{ inputs.name }} → {"name"}
    - multiple references → all returned
    - uppercase input name → not matched (requires lowercase)
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    _bool,
    _first_forbidden_secret_path,
    _forbidden_secret_key,
    _forbidden_secret_value,
    _list_of_str,
    _looks_like_url,
    _workflow_input_refs,
    _workflow_secret_refs,
)

# ---------------------------------------------------------------------------
# 1. _bool
# ---------------------------------------------------------------------------

class TestBool:
    def test_bool_true_returned(self):
        assert _bool(True) is True

    def test_bool_false_returned(self):
        assert _bool(False) is False

    def test_string_true(self):
        assert _bool("true") is True

    def test_string_True_case_insensitive(self):
        assert _bool("TRUE") is True

    def test_string_yes(self):
        assert _bool("yes") is True

    def test_string_1(self):
        assert _bool("1") is True

    def test_string_false(self):
        assert _bool("false") is False

    def test_string_no(self):
        assert _bool("no") is False

    def test_string_zero(self):
        assert _bool("0") is False

    def test_string_other(self):
        assert _bool("maybe") is False

    def test_none_returns_default(self):
        assert _bool(None) is False

    def test_none_with_custom_default(self):
        assert _bool(None, default=True) is True

    def test_int_one_uses_default(self):
        # int 1 is not a bool — uses default
        assert _bool(1) is False

    def test_whitespace_stripped_before_check(self):
        assert _bool("  true  ") is True


# ---------------------------------------------------------------------------
# 2. _list_of_str
# ---------------------------------------------------------------------------

class TestListOfStr:
    def test_non_list_returns_empty(self):
        assert _list_of_str(None) == []
        assert _list_of_str("string") == []
        assert _list_of_str(42) == []

    def test_empty_list_returns_empty(self):
        assert _list_of_str([]) == []

    def test_list_of_strings_returned(self):
        result = _list_of_str(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_strings_stripped(self):
        result = _list_of_str(["  a  ", "  b  "])
        assert result == ["a", "b"]

    def test_empty_string_excluded(self):
        result = _list_of_str(["a", "", "b"])
        assert "" not in result
        assert "a" in result
        assert "b" in result

    def test_whitespace_only_excluded(self):
        result = _list_of_str(["a", "   ", "b"])
        assert "   " not in result
        assert len(result) == 2

    def test_non_string_items_excluded(self):
        result = _list_of_str(["valid", 42, None, "also_valid"])
        assert result == ["valid", "also_valid"]


# ---------------------------------------------------------------------------
# 3. _looks_like_url
# ---------------------------------------------------------------------------

class TestLooksLikeUrl:
    def test_https_url(self):
        assert _looks_like_url("https://example.com") is True

    def test_http_url(self):
        assert _looks_like_url("http://example.com") is True

    def test_no_scheme_returns_false(self):
        assert _looks_like_url("example.com") is False

    def test_none_returns_false(self):
        assert _looks_like_url(None) is False

    def test_empty_string_returns_false(self):
        assert _looks_like_url("") is False

    def test_url_with_spaces_returns_false(self):
        assert _looks_like_url("https://example .com") is False

    def test_plain_text_returns_false(self):
        assert _looks_like_url("some description text") is False

    def test_ftp_scheme_returns_false(self):
        assert _looks_like_url("ftp://files.example.com") is False


# ---------------------------------------------------------------------------
# 4. _forbidden_secret_key
# ---------------------------------------------------------------------------

class TestForbiddenSecretKey:
    def test_key_containing_secret(self):
        assert _forbidden_secret_key("my_secret_key") is True

    def test_key_containing_token(self):
        assert _forbidden_secret_key("api_token") is True

    def test_key_containing_password(self):
        assert _forbidden_secret_key("user_password") is True

    def test_key_containing_credential(self):
        assert _forbidden_secret_key("db_credentials") is True

    def test_key_containing_authorization(self):
        assert _forbidden_secret_key("authorization_header") is True

    def test_benign_key_returns_false(self):
        assert _forbidden_secret_key("app_name") is False

    def test_benign_url_key_returns_false(self):
        assert _forbidden_secret_key("base_url") is False

    def test_case_insensitive(self):
        assert _forbidden_secret_key("MY_SECRET_KEY") is True

    def test_mixed_case(self):
        assert _forbidden_secret_key("ApiToken") is True


# ---------------------------------------------------------------------------
# 5. _forbidden_secret_value
# ---------------------------------------------------------------------------

class TestForbiddenSecretValue:
    def test_pem_private_key(self):
        assert _forbidden_secret_value("-----BEGIN RSA PRIVATE KEY-----\nMIIE...") is True

    def test_github_personal_access_token(self):
        assert _forbidden_secret_value("ghp_abcdefghijklmnopqrstuvwxyz") is True

    def test_github_fine_grained_pat(self):
        assert _forbidden_secret_value("github_pat_abc123") is True

    def test_openai_sk_key(self):
        assert _forbidden_secret_value("sk-proj-abc123") is True

    def test_slack_bot_token(self):
        assert _forbidden_secret_value("xoxb-12345-abcdef") is True

    def test_slack_user_token(self):
        assert _forbidden_secret_value("xoxp-12345-abcdef") is True

    def test_env_ref_allowed(self):
        assert _forbidden_secret_value("env://MY_SECRET") is False

    def test_vault_ref_allowed(self):
        assert _forbidden_secret_value("vault://secret/myapp/key") is False

    def test_template_variable_allowed(self):
        assert _forbidden_secret_value("${MY_SECRET_KEY}") is False

    def test_empty_returns_false(self):
        assert _forbidden_secret_value("") is False

    def test_none_returns_false(self):
        assert _forbidden_secret_value(None) is False

    def test_benign_value_returns_false(self):
        assert _forbidden_secret_value("postgres://localhost:5432/mydb") is False

    def test_template_variable_with_surrounding_text_allowed(self):
        assert _forbidden_secret_value("prefix-${MY_KEY}-suffix") is False


# ---------------------------------------------------------------------------
# 6. _first_forbidden_secret_path
# ---------------------------------------------------------------------------

class TestFirstForbiddenSecretPath:
    def test_empty_dict_returns_none(self):
        assert _first_forbidden_secret_path({}) is None

    def test_empty_list_returns_none(self):
        assert _first_forbidden_secret_path([]) is None

    def test_forbidden_key_returns_path(self):
        result = _first_forbidden_secret_path({"api_secret": "value"})
        assert result == "api_secret"

    def test_forbidden_value_returns_path(self):
        result = _first_forbidden_secret_path({"api_key": "ghp_abc123"})
        assert result == "api_key"

    def test_nested_forbidden_key_dotted_path(self):
        result = _first_forbidden_secret_path({"database": {"password": "mypass"}})
        assert result == "database.password"

    def test_list_forbidden_value_indexed_path(self):
        result = _first_forbidden_secret_path(["safe", "ghp_abc123"])
        assert result == "[1]"

    def test_deeply_nested_dotted_path(self):
        result = _first_forbidden_secret_path({"a": {"b": {"api_token": "xyz"}}})
        assert result == "a.b.api_token"

    def test_benign_payload_returns_none(self):
        payload = {"name": "my_app", "url": "https://example.com", "version": "1.0"}
        assert _first_forbidden_secret_path(payload) is None

    def test_nested_list_in_dict(self):
        # _forbidden_secret_value checks str(list) which contains "ghp_", so key "items" is returned
        result = _first_forbidden_secret_path({"items": ["safe", "ghp_token_here"]})
        assert result == "items"


# ---------------------------------------------------------------------------
# 7. _workflow_secret_refs
# ---------------------------------------------------------------------------

class TestWorkflowSecretRefs:
    def test_no_refs_returns_empty_set(self):
        assert _workflow_secret_refs("plain workflow text") == set()

    def test_single_secret_ref(self):
        text = "uses: ${{ secrets.MY_SECRET }}"
        assert _workflow_secret_refs(text) == {"MY_SECRET"}

    def test_multiple_secret_refs(self):
        text = "key1: ${{ secrets.SECRET_A }}\nkey2: ${{ secrets.SECRET_B }}"
        result = _workflow_secret_refs(text)
        assert result == {"SECRET_A", "SECRET_B"}

    def test_duplicate_refs_deduplicated(self):
        text = "${{ secrets.MY_SECRET }} ${{ secrets.MY_SECRET }}"
        result = _workflow_secret_refs(text)
        assert result == {"MY_SECRET"}

    def test_lowercase_secret_name_not_matched(self):
        # _WORKFLOW_SECRET_REF_RE requires [A-Z][A-Z0-9_]*
        assert _workflow_secret_refs("${{ secrets.my_secret }}") == set()

    def test_spaces_around_name_matched(self):
        text = "${{  secrets.MY_KEY  }}"
        assert _workflow_secret_refs(text) == {"MY_KEY"}

    def test_none_text_returns_empty(self):
        assert _workflow_secret_refs(None) == set()


# ---------------------------------------------------------------------------
# 8. _workflow_input_refs
# ---------------------------------------------------------------------------

class TestWorkflowInputRefs:
    def test_no_refs_returns_empty_set(self):
        assert _workflow_input_refs("plain workflow text") == set()

    def test_single_input_ref(self):
        text = "uses: ${{ inputs.my_input }}"
        assert _workflow_input_refs(text) == {"my_input"}

    def test_multiple_input_refs(self):
        text = "key1: ${{ inputs.image_tag }}\nkey2: ${{ inputs.environment }}"
        result = _workflow_input_refs(text)
        assert result == {"image_tag", "environment"}

    def test_uppercase_input_name_not_matched(self):
        # _WORKFLOW_INPUT_REF_RE requires [a-z][a-z0-9_]*
        assert _workflow_input_refs("${{ inputs.MY_INPUT }}") == set()

    def test_spaces_around_name_matched(self):
        text = "${{  inputs.build_tag  }}"
        assert _workflow_input_refs(text) == {"build_tag"}

    def test_none_text_returns_empty(self):
        assert _workflow_input_refs(None) == set()
