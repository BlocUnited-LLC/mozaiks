"""Adversarial tests for the mozaiks.canonical_json.v1 serialization contract."""

from __future__ import annotations

import copy
import subprocess
import sys

import pytest

from mozaiksai.core.semantics.canonical import (
    CANONICAL_SERIALIZATION_VERSION,
    CanonicalSerializationError,
    canonical_digest,
    canonical_json,
)

GOLDEN_VALUE = {"z": [3, 1.5, True, None], "a": {"nested": "café"}, "k": "x"}
GOLDEN_JSON = '{"a":{"nested":"caf\\u00e9"},"k":"x","z":[3,1.5,true,null]}'
GOLDEN_DIGEST = "a5038c06a5e632670660d86f9a20fe546be5f44669b77596ab57180b2b108d49"

EMPTY_LIST_DIGEST = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def test_serialization_contract_is_versioned() -> None:
    assert CANONICAL_SERIALIZATION_VERSION == "mozaiks.canonical_json.v1"


def test_golden_vector_bytes_and_digest() -> None:
    assert canonical_json(GOLDEN_VALUE) == GOLDEN_JSON
    assert canonical_digest(GOLDEN_VALUE) == GOLDEN_DIGEST
    assert canonical_digest([]) == EMPTY_LIST_DIGEST


def test_repeated_serialization_is_byte_identical() -> None:
    outputs = {canonical_json(GOLDEN_VALUE) for _ in range(50)}
    assert outputs == {GOLDEN_JSON}


def test_mapping_key_order_does_not_affect_bytes_or_digest() -> None:
    reordered = {"k": "x", "a": {"nested": "café"}, "z": [3, 1.5, True, None]}
    assert canonical_json(reordered) == GOLDEN_JSON
    assert canonical_digest(reordered) == GOLDEN_DIGEST


def test_digest_stable_across_process_restart() -> None:
    script = (
        "from mozaiksai.core.semantics.canonical import canonical_digest;"
        "print(canonical_digest({'z': [3, 1.5, True, None],"
        " 'a': {'nested': 'caf\\u00e9'}, 'k': 'x'}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == GOLDEN_DIGEST


def test_unicode_is_escaped_to_ascii() -> None:
    text = canonical_json({"emoji": "\U0001f600", "accent": "é"})
    assert text.isascii()
    assert "\\ud83d\\ude00" in text
    assert "\\u00e9" in text


def test_semantically_ordered_collections_preserve_order() -> None:
    assert canonical_json([3, 1, 2]) == "[3,1,2]"
    assert canonical_digest([3, 1, 2]) != canonical_digest([1, 2, 3])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_fail_closed(bad: float) -> None:
    with pytest.raises(CanonicalSerializationError, match="non-finite"):
        canonical_json({"x": bad})


def test_negative_zero_normalizes() -> None:
    assert canonical_json({"x": -0.0}) == '{"x":0.0}'
    assert canonical_digest({"x": -0.0}) == canonical_digest({"x": 0.0})


def test_int_and_bool_are_distinct() -> None:
    assert canonical_json({"x": True}) == '{"x":true}'
    assert canonical_json({"x": 1}) == '{"x":1}'
    assert canonical_digest({"x": True}) != canonical_digest({"x": 1})


def test_integers_outside_int64_fail_closed() -> None:
    with pytest.raises(CanonicalSerializationError, match="64-bit"):
        canonical_json({"x": 2**63})
    with pytest.raises(CanonicalSerializationError, match="64-bit"):
        canonical_json({"x": -(2**63) - 1})
    assert canonical_json({"x": 2**63 - 1}) == f'{{"x":{2**63 - 1}}}'


def test_non_string_mapping_keys_fail_closed() -> None:
    with pytest.raises(CanonicalSerializationError, match="mapping key"):
        canonical_json({1: "x"})


@pytest.mark.parametrize("bad", [{1, 2}, frozenset({1}), b"bytes", object()])
def test_unsupported_types_fail_closed(bad: object) -> None:
    with pytest.raises(CanonicalSerializationError, match="unsupported type"):
        canonical_json({"x": bad})


def test_input_objects_are_not_mutated() -> None:
    value = {"z": [3, {"inner": [1, 2]}], "a": {"nested": "café"}}
    snapshot = copy.deepcopy(value)
    canonical_json(value)
    canonical_digest(value)
    assert value == snapshot
    assert list(value.keys()) == list(snapshot.keys())


def test_tuple_and_list_serialize_identically() -> None:
    assert canonical_json((1, 2)) == canonical_json([1, 2])
