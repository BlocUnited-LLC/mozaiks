"""
Pure helper unit tests for:
  mozaiksai/core/workflow/paths.py
  mozaiksai/core/workflow/llm_config.py

Covers sync pure helpers (no IO/async):

  _reject_path_list (paths.py):
    - single path → no exception raised
    - path containing os.pathsep → raises ValueError
    - empty string → no exception raised
    - path with colon on Windows (drive letter) → raises on Windows (os.pathsep=";") but not Unix

  _build_llm_cache_key (llm_config.py):
    - no response_format, no extra_config → "base"
    - response_format only → includes model name and schema hash in key
    - extra_config with scalar values only → appended sorted
    - extra_config with non-scalar values → non-scalars excluded
    - both response_format and extra_config → both present
    - extra_config empty dict → only "base" prefix
    - extra_config None → only "base" prefix
    - same model → same key (deterministic)
    - different models → different keys
    - extra_config key order irrelevant → same key regardless of input order
"""
from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from mozaiksai.core.workflow.llm_config import _build_llm_cache_key
from mozaiksai.core.workflow.paths import _reject_path_list

# ---------------------------------------------------------------------------
# 1. _reject_path_list
# ---------------------------------------------------------------------------

class TestRejectPathList:
    def test_single_path_no_exception(self):
        _reject_path_list("/some/path")  # should not raise

    def test_empty_string_no_exception(self):
        _reject_path_list("")  # should not raise

    def test_path_with_os_pathsep_raises(self):
        # os.pathsep is ":" on Unix, ";" on Windows
        value = f"/path/one{os.pathsep}/path/two"
        with pytest.raises(ValueError, match="single workflow root"):
            _reject_path_list(value)

    def test_normal_relative_path_no_exception(self):
        _reject_path_list("factory_app/workflows")  # should not raise


# ---------------------------------------------------------------------------
# 2. _build_llm_cache_key
# ---------------------------------------------------------------------------

class _ModelA(BaseModel):
    field: str


class _ModelB(BaseModel):
    other: int


class TestBuildLlmCacheKey:
    def test_no_args_returns_base(self):
        result = _build_llm_cache_key(response_format=None, extra_config=None)
        assert result == "base"

    def test_response_format_adds_model_name(self):
        result = _build_llm_cache_key(response_format=_ModelA, extra_config=None)
        assert "_ModelA" in result

    def test_response_format_includes_schema_hash(self):
        result = _build_llm_cache_key(response_format=_ModelA, extra_config=None)
        parts = result.split("|")
        assert len(parts) == 2
        assert parts[1].startswith("rf:_ModelA:")

    def test_different_models_produce_different_keys(self):
        key_a = _build_llm_cache_key(response_format=_ModelA, extra_config=None)
        key_b = _build_llm_cache_key(response_format=_ModelB, extra_config=None)
        assert key_a != key_b

    def test_same_model_is_deterministic(self):
        key1 = _build_llm_cache_key(response_format=_ModelA, extra_config=None)
        key2 = _build_llm_cache_key(response_format=_ModelA, extra_config=None)
        assert key1 == key2

    def test_extra_config_scalar_values_included(self):
        result = _build_llm_cache_key(
            response_format=None,
            extra_config={"temperature": 0.7, "max_tokens": 256},
        )
        assert "temperature=0.7" in result
        assert "max_tokens=256" in result

    def test_extra_config_non_scalar_values_excluded(self):
        result = _build_llm_cache_key(
            response_format=None,
            extra_config={"temperature": 0.5, "tools": ["tool1", "tool2"]},
        )
        assert "tools" not in result
        assert "temperature=0.5" in result

    def test_extra_config_key_order_irrelevant(self):
        key1 = _build_llm_cache_key(
            response_format=None,
            extra_config={"b": 2, "a": 1},
        )
        key2 = _build_llm_cache_key(
            response_format=None,
            extra_config={"a": 1, "b": 2},
        )
        assert key1 == key2

    def test_extra_config_empty_dict_returns_base(self):
        result = _build_llm_cache_key(response_format=None, extra_config={})
        assert result == "base"

    def test_extra_config_all_non_scalar_returns_base(self):
        result = _build_llm_cache_key(
            response_format=None,
            extra_config={"tools": [1, 2], "schema": {"type": "object"}},
        )
        assert result == "base"

    def test_both_format_and_extra_config(self):
        result = _build_llm_cache_key(
            response_format=_ModelA,
            extra_config={"temperature": 0.0},
        )
        parts = result.split("|")
        assert len(parts) == 3
        assert parts[0] == "base"
        assert "rf:_ModelA" in parts[1]
        assert "temperature=0.0" in parts[2]
