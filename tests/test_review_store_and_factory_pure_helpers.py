"""
Pure helper unit tests for:
  mozaiksai/core/workflow/artifacts/review_store.py
  mozaiksai/core/workflow/agents/factory.py

Covers (review_store.py):
  _resolve_namespace:
    - None → DEFAULT_REVIEW_ARTIFACT_NAMESPACE
    - empty string → DEFAULT_REVIEW_ARTIFACT_NAMESPACE
    - whitespace only → DEFAULT_REVIEW_ARTIFACT_NAMESPACE
    - non-empty string returned as-is (stripped)

  _resolve_artifact_id:
    - returns first non-empty field value from id_fields
    - custom id_fields respected
    - all fields empty → ""
    - artifact_id preferred over other fields by default order

  _resolve_review_url:
    - explicit review_url → returned as-is
    - artifact["review_url"] used if review_url param not provided
    - template formatted with artifact_id
    - nothing available → ""

Covers (agents/factory.py):
  _compose_prompt_sections:
    - list of sections with heading+content
    - list of sections with content only
    - non-dict sections skipped
    - dict-style input (not list) → reorders by section_order keys
    - heading dict with no content → heading only
    - multiple sections joined with double newline
    - empty list → ""
    - non-string content coerced to str
"""
from __future__ import annotations

from mozaiksai.core.workflow.agents.factory import _compose_prompt_sections
from mozaiksai.core.workflow.artifacts.review_store import (
    DEFAULT_REVIEW_ARTIFACT_NAMESPACE,
    _resolve_artifact_id,
    _resolve_namespace,
    _resolve_review_url,
)

# ---------------------------------------------------------------------------
# 1. _resolve_namespace
# ---------------------------------------------------------------------------

class TestResolveNamespace:
    def test_none_returns_default(self):
        assert _resolve_namespace(None) == DEFAULT_REVIEW_ARTIFACT_NAMESPACE

    def test_empty_string_returns_default(self):
        assert _resolve_namespace("") == DEFAULT_REVIEW_ARTIFACT_NAMESPACE

    def test_whitespace_only_returns_default(self):
        assert _resolve_namespace("   ") == DEFAULT_REVIEW_ARTIFACT_NAMESPACE

    def test_custom_namespace_returned(self):
        assert _resolve_namespace("my_namespace") == "my_namespace"

    def test_strips_whitespace(self):
        assert _resolve_namespace("  my_namespace  ") == "my_namespace"

    def test_default_namespace_value(self):
        assert DEFAULT_REVIEW_ARTIFACT_NAMESPACE == "workflow_review_artifacts"


# ---------------------------------------------------------------------------
# 2. _resolve_artifact_id
# ---------------------------------------------------------------------------

class TestResolveArtifactId:
    def test_artifact_id_field_returned(self):
        artifact = {"artifact_id": "art-001"}
        assert _resolve_artifact_id(artifact) == "art-001"

    def test_proposal_id_returned_when_no_artifact_id(self):
        artifact = {"proposal_id": "prop-001"}
        assert _resolve_artifact_id(artifact) == "prop-001"

    def test_review_id_returned_when_no_other_id(self):
        artifact = {"review_id": "rev-001"}
        assert _resolve_artifact_id(artifact) == "rev-001"

    def test_id_returned_as_fallback(self):
        artifact = {"id": "generic-001"}
        assert _resolve_artifact_id(artifact) == "generic-001"

    def test_first_field_wins(self):
        artifact = {"artifact_id": "art-001", "proposal_id": "prop-001"}
        assert _resolve_artifact_id(artifact) == "art-001"

    def test_all_empty_returns_empty(self):
        assert _resolve_artifact_id({}) == ""

    def test_custom_id_fields(self):
        artifact = {"custom_key": "custom-001"}
        assert _resolve_artifact_id(artifact, id_fields=["custom_key"]) == "custom-001"

    def test_none_values_skipped(self):
        artifact = {"artifact_id": None, "proposal_id": "prop-001"}
        assert _resolve_artifact_id(artifact) == "prop-001"

    def test_strips_whitespace(self):
        artifact = {"artifact_id": "  art-001  "}
        assert _resolve_artifact_id(artifact) == "art-001"


# ---------------------------------------------------------------------------
# 3. _resolve_review_url
# ---------------------------------------------------------------------------

class TestResolveReviewUrl:
    def test_explicit_review_url_returned(self):
        artifact = {}
        result = _resolve_review_url(artifact, artifact_id="art-001", review_url="https://review.example.com/art-001", review_url_template=None)
        assert result == "https://review.example.com/art-001"

    def test_artifact_review_url_used_when_no_param(self):
        artifact = {"review_url": "https://from-artifact.example.com"}
        result = _resolve_review_url(artifact, artifact_id="art-001", review_url=None, review_url_template=None)
        assert result == "https://from-artifact.example.com"

    def test_template_formatted_with_artifact_id(self):
        artifact = {}
        result = _resolve_review_url(
            artifact,
            artifact_id="art-001",
            review_url=None,
            review_url_template="https://review.example.com/{artifact_id}",
        )
        assert result == "https://review.example.com/art-001"

    def test_nothing_available_returns_empty(self):
        artifact = {}
        result = _resolve_review_url(artifact, artifact_id="art-001", review_url=None, review_url_template=None)
        assert result == ""

    def test_explicit_url_takes_precedence_over_template(self):
        artifact = {}
        result = _resolve_review_url(
            artifact,
            artifact_id="art-001",
            review_url="https://explicit.example.com",
            review_url_template="https://template.example.com/{artifact_id}",
        )
        assert result == "https://explicit.example.com"

    def test_strips_whitespace_from_url(self):
        artifact = {}
        result = _resolve_review_url(
            artifact,
            artifact_id="art-001",
            review_url="  https://review.example.com  ",
            review_url_template=None,
        )
        assert result == "https://review.example.com"


# ---------------------------------------------------------------------------
# 4. _compose_prompt_sections (agents/factory.py)
# ---------------------------------------------------------------------------

class TestComposePromptSectionsFactory:
    def test_empty_list_returns_empty(self):
        assert _compose_prompt_sections([]) == ""

    def test_section_with_heading_and_content(self):
        sections = [{"heading": "## Role", "content": "You are a helper."}]
        result = _compose_prompt_sections(sections)
        assert "## Role" in result
        assert "You are a helper." in result

    def test_section_with_content_only(self):
        sections = [{"content": "Only content here."}]
        result = _compose_prompt_sections(sections)
        assert result == "Only content here."

    def test_section_with_heading_only(self):
        sections = [{"heading": "## Heading"}]
        result = _compose_prompt_sections(sections)
        assert result == "## Heading"

    def test_non_dict_sections_skipped(self):
        sections = ["not a dict", {"content": "valid"}]
        result = _compose_prompt_sections(sections)
        assert result == "valid"

    def test_multiple_sections_double_newline(self):
        sections = [{"content": "First"}, {"content": "Second"}]
        result = _compose_prompt_sections(sections)
        assert "First" in result
        assert "Second" in result
        assert "\n\n" in result

    def test_dict_style_input_with_known_keys(self):
        # Dict input with standard section keys gets reordered
        sections = {
            "objective": {"content": "Objective text", "heading": ""},
            "role": {"content": "Role text", "heading": ""},
        }
        result = _compose_prompt_sections(sections)
        # role comes before objective in section_order
        assert result.index("Role text") < result.index("Objective text")

    def test_dict_style_unknown_keys_ignored(self):
        # Dict input with unknown keys → renders nothing from those
        sections = {"unknown_key": {"content": "Should be ignored"}}
        result = _compose_prompt_sections(sections)
        assert "Should be ignored" not in result

    def test_non_string_content_coerced(self):
        sections = [{"content": 42}]
        result = _compose_prompt_sections(sections)
        assert result == "42"

    def test_empty_content_heading_still_rendered(self):
        sections = [{"heading": "## Title", "content": ""}]
        result = _compose_prompt_sections(sections)
        assert result == "## Title"
