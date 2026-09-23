"""Session extra fields must be BSON-encodable at every level, not just the top.

The 2026-09-23 acceptance run at OSS 074cfcdf cleared six gates -- including an
accepted subscription contract -- and died handing off to AppGenerator:

    Failed to create chat session 4fce...: Invalid document: documents must
      have only string keys, key was 401
    [JOURNEY] handle_run_complete failed: ... key was 401

`create_chat_session` already skipped non-string keys, but only at the top
level:

    for k, v in list(extra_fields.items()):
        if not isinstance(k, str) or not k.strip():
            continue          # guards k
        session_doc[k] = v    # v's nested keys go straight to the driver

The journey handoff carries ~105 accumulated context keys, including whole
generated workflow bundles. A non-string key is trivial to produce there:
unquoted `401:` in generated YAML parses as an int. One of them fails the
insert, and on a handoff that ends the build.

Coercing rather than dropping is what the data already means -- these
structures originate as JSON structured outputs, where keys are strings by
definition, and they are serialized back to JSON over the transport, which
stringifies keys anyway. Dropping would silently lose generated content.

The driver error names the key but not the path. With a hundred context keys
carrying whole bundles, "key was 401" is not findable, so the coercion is
logged with a dotted path.
"""

from __future__ import annotations

import bson
import pytest

from mozaiksai.core.data.persistence.persistence_manager import (
    bson_safe_keys,
    find_non_string_keys,
)

# The shape the live run produced: a generated bundle carrying HTTP status
# codes as integer keys, which is what unquoted `401:` in YAML becomes.
LIVE_FIELD = {"bundles": [{"tools": {"responses": {401: "unauthorized", 200: "ok"}}}]}


def test_the_live_payload_becomes_encodable() -> None:
    """The regression, stated as the driver states it."""
    with pytest.raises(bson.errors.InvalidDocument, match="key was 401"):
        bson.encode({"_id": "c1", "workflow_bundle_results": LIVE_FIELD})

    bson.encode({"_id": "c1", "workflow_bundle_results": bson_safe_keys(LIVE_FIELD)})


def test_the_coerced_keys_keep_their_values() -> None:
    responses = bson_safe_keys(LIVE_FIELD)["bundles"][0]["tools"]["responses"]
    assert responses == {"401": "unauthorized", "200": "ok"}


def test_the_offending_path_is_reportable() -> None:
    """The driver says 'key was 401' and nothing else; this is what makes it findable."""
    offenders = find_non_string_keys(LIVE_FIELD, _path="workflow_bundle_results")
    assert any("workflow_bundle_results.bundles[0].tools.responses.401" in o for o in offenders)
    assert any("is int" in o for o in offenders), "name the type, so a tuple key reads differently"


def test_clean_data_is_returned_unchanged() -> None:
    """Coercion must be a no-op for everything that was already valid."""
    clean = {
        "a": [1, 2, {"b": None, "c": True}],
        "d": "x",
        "e": 3.5,
        "nested": {"deep": {"deeper": ["s", 1, None]}},
    }
    assert bson_safe_keys(clean) == clean
    assert find_non_string_keys(clean) == []


def test_keys_are_coerced_at_every_depth() -> None:
    deep = {"a": {"b": [{"c": {7: {"d": {None: "x"}}}}]}}
    safe = bson_safe_keys(deep)
    assert safe["a"]["b"][0]["c"]["7"]["d"]["None"] == "x"
    bson.encode({"_id": "c1", **safe})


def test_tuples_become_lists_so_they_encode() -> None:
    """Frozen context yields tuples; BSON has no tuple type."""
    safe = bson_safe_keys({"items": ("a", "b")})
    assert safe["items"] == ["a", "b"]
    bson.encode({"_id": "c1", **safe})


def test_a_colliding_coercion_does_not_lose_the_document() -> None:
    """`{1: 'a', '1': 'b'}` cannot round-trip; the document must still encode.

    Both keys becoming '1' is lossy, but the alternative is failing the insert
    and ending the build over a generated artifact's key type. Pinned so the
    behaviour is a decision rather than a surprise.
    """
    safe = bson_safe_keys({1: "a", "1": "b"})
    assert list(safe) == ["1"]
    bson.encode({"_id": "c1", **safe})


def test_the_write_path_applies_it() -> None:
    """Guard the wiring: the helper is useless if create_chat_session stops calling it."""
    import inspect

    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

    source = inspect.getsource(AG2PersistenceManager.create_chat_session)
    assert "bson_safe_keys(" in source, "extra fields must be coerced before insert_one"
    assert source.index("bson_safe_keys(") < source.index("insert_one"), (
        "coercion must happen before the write, not after"
    )
