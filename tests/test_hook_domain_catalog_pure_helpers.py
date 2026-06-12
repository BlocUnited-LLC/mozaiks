"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_domain_catalog_context.py

Covers:
  _collect_concept_text:
    - empty context/messages → ""
    - concept_overview string key → included in text
    - dict value → stringified and included
    - recent user messages (last 4) included
    - non-user role messages skipped
    - result is lowercased
    - keys: concept_overview, concept_blueprint, design_surface_map, refinement_request

  _score_domain:
    - empty concept_text → 0
    - domain key normalized and matched → +10
    - description word overlap → +1 per word (len > 4)
    - common_app_types match → +5 per match
    - module key match → +3 per match
    - non-matching domain → 0

  _select_top_domains:
    - empty domains → []
    - domains sorted by score descending
    - result capped at _TOP_N_DOMAINS (4)
    - non-dict domain data skipped
    - exact top domain key in result

  _format_global_base:
    - empty catalog → ""
    - no global_base key → ""
    - no modules → ""
    - modules rendered as bullets
    - description truncated at first period

  _format_domain_excerpt:
    - domain key in header line
    - description in header line
    - common_app_types rendered (up to 4)
    - capability_packs rendered
    - modules rendered (up to _MAX_MODULES_PER_DOMAIN=5)
    - overflow modules → "... and N more modules"
    - module recommended_module_type shown
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_domain_catalog_context import (
    _collect_concept_text,
    _format_domain_excerpt,
    _format_global_base,
    _score_domain,
    _select_top_domains,
)

# ---------------------------------------------------------------------------
# 1. _collect_concept_text
# ---------------------------------------------------------------------------

class TestCollectConceptText:
    def test_empty_context_and_messages_returns_empty_string(self):
        result = _collect_concept_text({}, [])
        assert result == "" or result.strip() == ""

    def test_concept_overview_string_included(self):
        ctx = {"concept_overview": "A marketplace for artists"}
        result = _collect_concept_text(ctx, [])
        assert "marketplace" in result

    def test_dict_value_stringified(self):
        ctx = {"concept_blueprint": {"feature": "payments"}}
        result = _collect_concept_text(ctx, [])
        assert "payments" in result

    def test_user_messages_included(self):
        msgs = [{"role": "user", "content": "I want an ecommerce app"}]
        result = _collect_concept_text({}, msgs)
        assert "ecommerce" in result

    def test_non_user_messages_skipped(self):
        msgs = [{"role": "assistant", "content": "I will help you"}]
        result = _collect_concept_text({}, msgs)
        # assistant content should not appear
        assert "i will help you" not in result

    def test_result_is_lowercased(self):
        ctx = {"concept_overview": "ECommerce Platform"}
        result = _collect_concept_text(ctx, [])
        assert "ecommerce" in result.lower()
        assert "ECommerce" not in result

    def test_only_last_four_user_messages(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(6)]
        result = _collect_concept_text({}, msgs)
        # Last 4 messages are msg2-msg5
        assert "msg5" in result
        assert "msg0" not in result

    def test_refinement_request_included(self):
        ctx = {"refinement_request": "Add subscription billing"}
        result = _collect_concept_text(ctx, [])
        assert "subscription" in result


# ---------------------------------------------------------------------------
# 2. _score_domain
# ---------------------------------------------------------------------------

class TestScoreDomain:
    def test_empty_concept_text_returns_zero(self):
        assert _score_domain("ecommerce", {}, "") == 0

    def test_domain_key_in_text_adds_ten(self):
        score = _score_domain("ecommerce", {}, "i want an ecommerce platform")
        assert score >= 10

    def test_domain_key_with_underscore_normalized(self):
        # "real_estate" → "real estate" normalized
        score = _score_domain("real_estate", {}, "this is a real estate listing app")
        assert score >= 10

    def test_description_word_overlap_adds_one_per_word(self):
        domain_data = {"description": "Platform for selling products online"}
        score = _score_domain("shop", domain_data, "selling products online means")
        # "selling" (7 chars), "products" (8 chars), "online" (6 chars) each match → +3
        assert score >= 3

    def test_short_description_words_skipped(self):
        # "for", "the", "an" are 3 chars or fewer → skipped
        domain_data = {"description": "For the an app"}
        score = _score_domain("shop", domain_data, "for the an app")
        assert score == 0  # all words ≤4 chars

    def test_common_app_types_match_adds_five(self):
        domain_data = {"common_app_types": ["booking platform"]}
        score = _score_domain("travel", domain_data, "build a booking platform for hotels")
        assert score >= 5

    def test_module_key_match_adds_three(self):
        domain_data = {"modules": {"inventory": {}, "orders": {}}}
        # "inventory" in concept text → +3
        score = _score_domain("shop", domain_data, "need inventory management")
        assert score >= 3

    def test_module_key_underscore_normalized(self):
        domain_data = {"modules": {"order_history": {}}}
        score = _score_domain("shop", domain_data, "need order history tracking")
        assert score >= 3


# ---------------------------------------------------------------------------
# 3. _select_top_domains
# ---------------------------------------------------------------------------

class TestSelectTopDomains:
    def test_empty_domains_returns_empty(self):
        catalog = {"domains": {}}
        assert _select_top_domains(catalog, "anything") == []

    def test_non_dict_domain_data_skipped(self):
        catalog = {"domains": {"bad": "not-a-dict", "ok": {}}}
        result = _select_top_domains(catalog, "anything")
        assert all(isinstance(data, dict) for _, data in result)

    def test_best_matching_domain_first(self):
        catalog = {
            "domains": {
                "ecommerce": {"description": "Online shopping platform"},
                "healthcare": {"description": "Medical records"},
            }
        }
        result = _select_top_domains(catalog, "building an online shopping cart")
        assert result[0][0] == "ecommerce"

    def test_result_capped_at_four(self):
        domains = {f"domain_{i}": {"description": f"domain {i}"} for i in range(10)}
        catalog = {"domains": domains}
        result = _select_top_domains(catalog, "test")
        assert len(result) <= 4

    def test_returns_key_data_tuples(self):
        catalog = {"domains": {"ecommerce": {"description": "Shop"}}}
        result = _select_top_domains(catalog, "ecommerce")
        assert len(result) == 1
        assert result[0][0] == "ecommerce"
        assert isinstance(result[0][1], dict)

    def test_no_domains_key_returns_empty(self):
        assert _select_top_domains({}, "test") == []


# ---------------------------------------------------------------------------
# 4. _format_global_base
# ---------------------------------------------------------------------------

class TestFormatGlobalBase:
    def test_empty_catalog_returns_empty_string(self):
        assert _format_global_base({}) == ""

    def test_no_global_base_key_returns_empty(self):
        assert _format_global_base({"other": {}}) == ""

    def test_no_modules_returns_empty(self):
        assert _format_global_base({"global_base": {}}) == ""

    def test_empty_modules_dict_returns_empty(self):
        assert _format_global_base({"global_base": {"modules": {}}}) == ""

    def test_module_key_rendered(self):
        catalog = {"global_base": {"modules": {"auth": {"description": "Authentication."}}}}
        result = _format_global_base(catalog)
        assert "auth" in result

    def test_description_truncated_at_period(self):
        catalog = {
            "global_base": {"modules": {"auth": {"description": "Authentication module. More text here."}}}
        }
        result = _format_global_base(catalog)
        assert "Authentication module" in result
        assert "More text here" not in result

    def test_header_line_present(self):
        catalog = {"global_base": {"modules": {"auth": {"description": "Auth."}}}}
        result = _format_global_base(catalog)
        assert "Global base modules" in result


# ---------------------------------------------------------------------------
# 5. _format_domain_excerpt
# ---------------------------------------------------------------------------

class TestFormatDomainExcerpt:
    def test_domain_key_in_header(self):
        result = _format_domain_excerpt("ecommerce", {})
        assert "ecommerce" in result

    def test_description_in_header(self):
        result = _format_domain_excerpt("ecommerce", {"description": "Online shopping"})
        assert "Online shopping" in result

    def test_common_app_types_rendered(self):
        data = {"common_app_types": ["Online store", "Marketplace"]}
        result = _format_domain_excerpt("ecommerce", data)
        assert "Online store" in result
        assert "Marketplace" in result

    def test_common_app_types_capped_at_four(self):
        types = [f"type_{i}" for i in range(6)]
        result = _format_domain_excerpt("ecommerce", {"common_app_types": types})
        assert "type_3" in result
        assert "type_4" not in result

    def test_capability_packs_rendered(self):
        data = {"capability_packs": ["stripe_pay", "sendgrid"]}
        result = _format_domain_excerpt("ecommerce", data)
        assert "stripe_pay" in result
        assert "sendgrid" in result

    def test_modules_rendered(self):
        data = {"modules": {"orders": {"description": "Order tracking."}, "inventory": {"description": "Stock management."}}}
        result = _format_domain_excerpt("ecommerce", data)
        assert "orders" in result
        assert "inventory" in result

    def test_modules_capped_at_five(self):
        modules = {f"module_{i}": {"description": f"Module {i}."} for i in range(7)}
        result = _format_domain_excerpt("ecommerce", {"modules": modules})
        assert "module_4" in result
        assert "module_5" not in result
        assert "more modules" in result

    def test_overflow_message_includes_count(self):
        modules = {f"m_{i}": {"description": "desc."} for i in range(7)}
        result = _format_domain_excerpt("ecommerce", {"modules": modules})
        assert "2 more modules" in result

    def test_module_recommended_type_rendered(self):
        data = {"modules": {"orders": {"description": "Orders.", "recommended_module_type": "data_module"}}}
        result = _format_domain_excerpt("ecommerce", data)
        assert "data_module" in result
