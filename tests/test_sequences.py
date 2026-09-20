from __future__ import annotations

from mozaiksai.core.utils.sequences import dedupe_strings


def test_keeps_first_occurrence_in_order():
    assert dedupe_strings(["b", "a", "b", "c"]) == ["b", "a", "c"]


def test_strips_before_comparing():
    assert dedupe_strings(["  a  ", "a", "x", "x "]) == ["a", "x"]


def test_drops_blanks_and_none():
    assert dedupe_strings(["", "a", None, "   "]) == ["a"]


def test_is_case_sensitive():
    # The fourteen call sites this replaced all treated case as significant.
    assert dedupe_strings(["A", "a"]) == ["A", "a"]


def test_empty_and_none_inputs():
    assert dedupe_strings([]) == []
    assert dedupe_strings(None) == []


def test_coerces_non_strings():
    assert dedupe_strings([1, "1", 2]) == ["1", "2"]
