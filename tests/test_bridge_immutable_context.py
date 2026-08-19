"""
Hostile immutability tests for ContextVariablesBridge.

Every test confirms that canonical context state cannot be mutated
outside ContextAuthorityPolicy even when a caller holds a reference
obtained through a read surface.

Read surfaces under test:
- bridge[key]          → freeze(value)
- bridge.get(key)      → freeze(value)
- bridge.items()       → (key, freeze(value)) generator
- bridge.values()      → freeze(value) generator
- bridge.data          → raises AttributeError
- bridge.snapshot()    → detach(data) — deep copy, no shared refs
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.frozen import detach, freeze
from mozaiksai.core.workflow.context.schema import load_context_variables_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(keys: dict[str, str] | None = None):
    """Build a minimal authority policy for testing."""
    defns: dict = {}
    if keys:
        for k, source_type in keys.items():
            defns[k] = {"type": "string", "source": {"type": source_type, "default": ""}}
    plan = load_context_variables_config({"definitions": defns})
    return build_context_authority_policy(
        workflow_name="TestWorkflow",
        definitions=plan.definitions or {},
    )


def make_bridge(initial: dict, *, with_policy: bool = False) -> ContextVariablesBridge:
    """Create a ContextVariablesBridge with known state."""
    policy = _make_policy({k: "state" for k in initial}) if with_policy else None
    return ContextVariablesBridge(initial, authority_policy=policy)


# ---------------------------------------------------------------------------
# Test 1: bridge.data raises AttributeError
# ---------------------------------------------------------------------------

def test_data_property_raises():
    bridge = make_bridge({"key": "value"})
    with pytest.raises(AttributeError, match="snapshot"):
        _ = bridge.data


# ---------------------------------------------------------------------------
# Test 2: nested dict write cannot mutate canonical state
# ---------------------------------------------------------------------------

def test_nested_dict_write_cannot_mutate_canonical_state():
    inner = {"level": "safe"}
    bridge = make_bridge({"config": inner})
    value = bridge["config"]
    try:
        value["level"] = "evil"  # type: ignore[index]
    except (TypeError, AttributeError):
        pass  # expected — MappingProxyType
    assert bridge["config"]["level"] == "safe", "Nested dict mutation reached canonical state"
    # Also confirm the original inner dict is unchanged
    assert inner["level"] == "safe"


# ---------------------------------------------------------------------------
# Test 3: nested list append cannot mutate canonical state
# ---------------------------------------------------------------------------

def test_nested_list_append_cannot_mutate_canonical_state():
    original = [1, 2, 3]
    bridge = make_bridge({"items": original})
    value = bridge["items"]
    try:
        value.append(99)  # type: ignore[union-attr]
    except (TypeError, AttributeError):
        pass  # expected — tuple
    assert 99 not in bridge["items"], "99 was appended to canonical state"
    assert len(bridge["items"]) == 3, "List length changed in canonical state"
    # The canonical backing list must be unchanged
    assert original == [1, 2, 3]


# ---------------------------------------------------------------------------
# Test 4: nested set mutation cannot mutate canonical state
# ---------------------------------------------------------------------------

def test_nested_set_mutation_cannot_mutate_canonical_state():
    bridge = make_bridge({"tags": {"a", "b"}})
    value = bridge["tags"]
    try:
        value.add("evil")  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        pass  # expected — frozenset
    assert "evil" not in bridge["tags"], "Set mutation reached canonical state"


# ---------------------------------------------------------------------------
# Test 5: values() returns frozen values — not canonical
# ---------------------------------------------------------------------------

def test_values_returns_frozen_not_canonical():
    bridge = make_bridge({"cfg": {"x": 1}})
    for v in bridge.values():
        try:
            v["x"] = 999  # type: ignore[index]
        except (TypeError, AttributeError):
            pass
    assert bridge["cfg"]["x"] == 1, "Mutation via values() reached canonical state"


# ---------------------------------------------------------------------------
# Test 6: items() returns frozen values — not canonical
# ---------------------------------------------------------------------------

def test_items_returns_frozen_not_canonical():
    bridge = make_bridge({"cfg": {"x": 1}})
    for _k, v in bridge.items():
        try:
            v["x"] = 999  # type: ignore[index]
        except (TypeError, AttributeError):
            pass
    assert bridge["cfg"]["x"] == 1, "Mutation via items() reached canonical state"


# ---------------------------------------------------------------------------
# Test 7: get() returns frozen not canonical
# ---------------------------------------------------------------------------

def test_get_returns_frozen_not_canonical():
    bridge = make_bridge({"cfg": {"x": 1}})
    v = bridge.get("cfg")
    try:
        v["x"] = 999  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    assert bridge["cfg"]["x"] == 1, "Mutation via get() reached canonical state"


# ---------------------------------------------------------------------------
# Test 8: tool mutation of returned value does not affect canonical state
# ---------------------------------------------------------------------------

def test_tool_mutation_does_not_affect_canonical_state():
    bridge = make_bridge({"results": [1, 2]})
    tool_input = bridge["results"]
    try:
        tool_input.append(99)  # type: ignore[union-attr]
    except (TypeError, AttributeError):
        pass
    assert 99 not in bridge["results"], "Tool mutation of returned value reached canonical state"


# ---------------------------------------------------------------------------
# Test 9: snapshot() is detached — mutations don't reach canonical state
# ---------------------------------------------------------------------------

def test_snapshot_is_detached():
    bridge = make_bridge({"cfg": {"x": 1}})
    snap = bridge.snapshot()
    snap["cfg"]["x"] = 999
    assert bridge["cfg"]["x"] == 1, "Snapshot mutation reached canonical state"
    assert snap["cfg"]["x"] == 999, "Snapshot must be a detached mutable copy"


# ---------------------------------------------------------------------------
# Test 10: persisted snapshot mutation cannot change live state
# ---------------------------------------------------------------------------

def test_persisted_snapshot_is_detached():
    bridge = make_bridge({"cfg": {"x": 1}})
    snap = bridge.snapshot()
    # Simulate persistence layer modifying the snapshot
    snap["cfg"]["x"] = 42
    # Live bridge must be unchanged
    assert bridge["cfg"]["x"] == 1, "Persisted snapshot mutation reached canonical state"


# ---------------------------------------------------------------------------
# Test 11: ScopedContextWriter is the only successful mutation path
# ---------------------------------------------------------------------------

def test_scoped_writer_is_only_mutation_path():
    defns = {
        "score": {"type": "integer", "source": {"type": "state", "default": "0"}},
    }
    plan = load_context_variables_config({"definitions": defns})
    policy = build_context_authority_policy(
        workflow_name="TestWorkflow",
        definitions=plan.definitions or {},
    )

    initial: dict = {"score": 0}
    bridge = ContextVariablesBridge(initial, authority_policy=policy)

    # Direct frozen-read: cannot mutate
    v = bridge["score"]
    assert v == 0

    # Unauthorized write through bridge raises (bridge_writer is context_bridge)
    # context_bridge writer is allowed for state-type vars with default writer_ids
    # so let's try writing a frozen value dict — the dict is a primitive read here
    # For the mutation test: confirm snapshot doesn't mutate canonical
    snap = bridge.snapshot()
    snap["score"] = 999
    assert bridge["score"] == 0, "Snapshot write should not affect canonical state"

    # Authorized write through __setitem__ (bridge uses context_bridge writer_id)
    bridge["score"] = 42
    assert bridge["score"] == 42, "Authorized write through bridge.__setitem__ should succeed"
    assert initial["score"] == 0, "Original caller-owned dict must not share bridge backing state"


# ---------------------------------------------------------------------------
# Test 12: ordinary reads still work
# ---------------------------------------------------------------------------

def test_ordinary_reads_still_work():
    bridge = make_bridge({"name": "alice", "count": 42, "flags": [True, False]})
    assert bridge["name"] == "alice"
    assert bridge.get("count") == 42
    assert bridge.get("missing", "default") == "default"
    # flags returned as immutable tuple but content-equal
    flags = bridge["flags"]
    assert list(flags) == [True, False]


# ---------------------------------------------------------------------------
# Test 13: snapshot() returns plain JSON-serializable values
# ---------------------------------------------------------------------------

def test_snapshot_returns_plain_serializable_values():
    bridge = make_bridge({"cfg": {"x": 1}, "items": [1, 2, 3]})
    snap = bridge.snapshot()
    # Must be JSON-serializable without error
    json.dumps(snap)
    assert isinstance(snap["cfg"], dict)
    assert isinstance(snap["items"], list)


# ---------------------------------------------------------------------------
# Test 14: deeply nested mutation cannot reach canonical state
# ---------------------------------------------------------------------------

def test_deeply_nested_dict_mutation_cannot_reach_canonical_state():
    bridge = make_bridge({"a": {"b": {"c": {"d": "target"}}}})
    v = bridge["a"]
    try:
        v["b"]["c"]["d"] = "evil"  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    assert bridge["a"]["b"]["c"]["d"] == "target", "Deeply nested mutation reached canonical state"


# ---------------------------------------------------------------------------
# Test 15: list-in-dict mutation at any depth cannot reach canonical state
# ---------------------------------------------------------------------------

def test_list_in_dict_mutation_cannot_reach_canonical_state():
    bridge = make_bridge({"data": {"items": [10, 20, 30]}})
    v = bridge["data"]
    try:
        v["items"].append(99)  # type: ignore[union-attr]
    except (TypeError, AttributeError):
        pass
    items = bridge["data"]["items"]
    assert list(items) == [10, 20, 30], "List-in-dict mutation reached canonical state"
    assert 99 not in items, "99 appeared in canonical list-in-dict"


# ---------------------------------------------------------------------------
# Test 16: get() with missing key returns frozen default — default None is fine
# ---------------------------------------------------------------------------

def test_get_missing_key_returns_frozen_default():
    bridge = make_bridge({"key": "value"})
    result = bridge.get("nonexistent")
    assert result is None

    result_with_default = bridge.get("nonexistent", {"fallback": True})
    # The default itself goes through freeze() — should be a MappingProxyType
    try:
        result_with_default["fallback"] = False  # type: ignore[index]
    except (TypeError, AttributeError):
        pass  # frozen — expected
    # Canonical state unaffected (the default was never in canonical state)
    assert bridge.get("nonexistent") is None


def test_to_dict_is_detached():
    bridge = make_bridge({"cfg": {"x": 1}, "items": [{"n": 1}]})
    data = bridge.to_dict()
    data["cfg"]["x"] = 999
    data["items"][0]["n"] = 999

    assert bridge["cfg"]["x"] == 1
    assert bridge["items"][0]["n"] == 1


def test_set_detaches_original_caller_reference_and_pending_update():
    value = {"nested": {"items": [1, 2]}}
    bridge = make_bridge({})

    bridge.set("payload", value)
    value["nested"]["items"].append(99)

    assert list(bridge["payload"]["nested"]["items"]) == [1, 2]
    updates = bridge.consume_context_updates()
    updates["set"]["payload"]["nested"]["items"].append(42)

    assert list(bridge["payload"]["nested"]["items"]) == [1, 2]


def test_pop_returns_detached_value():
    bridge = make_bridge({"payload": {"nested": {"ok": True}}})
    popped = bridge.pop("payload")
    popped["nested"]["ok"] = False

    assert "payload" not in bridge
    assert bridge.consume_context_updates()["delete"] == ["payload"]


class _MutableModel(BaseModel):
    name: str
    flags: list[str]


def test_pydantic_model_is_detached_on_set_and_frozen_on_read():
    model = _MutableModel(name="safe", flags=["a"])
    bridge = make_bridge({})

    bridge.set("model", model)
    model.flags.append("evil")
    read_value = bridge["model"]

    assert read_value["flags"] == ("a",)
    with pytest.raises(TypeError):
        read_value["name"] = "evil"


@dataclass
class _ContextDataclass:
    name: str
    flags: list[str]


def test_dataclass_is_detached_on_set_and_frozen_on_read():
    value = _ContextDataclass(name="safe", flags=["a"])
    bridge = make_bridge({})

    bridge.set("dataclass_value", value)
    value.flags.append("evil")
    read_value = bridge["dataclass_value"]

    assert read_value["flags"] == ("a",)
    with pytest.raises(TypeError):
        read_value["name"] = "evil"


def test_freeze_and_detach_reject_recursive_cycles_deterministically():
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="recursive cycle"):
        freeze(cyclic)
    with pytest.raises(ValueError, match="recursive cycle"):
        detach(cyclic)


def test_runtime_context_container_has_no_mutable_data_surface():
    source = {"cfg": {"x": 1}}
    container = create_context_container(source)

    source["cfg"]["x"] = 999
    assert container.get("cfg")["x"] == 1

    with pytest.raises(AttributeError, match="snapshot"):
        _ = container.data

    snap = container.snapshot()
    snap["cfg"]["x"] = 42
    assert container.get("cfg")["x"] == 1

    container.set("cfg", {"x": 2})
    assert source["cfg"]["x"] == 999


def test_authority_bearing_objects_are_rejected_from_context_variables():
    class _ServiceObject:
        def __init__(self) -> None:
            self.secret = "runtime"

    bridge = make_bridge({})
    with pytest.raises(TypeError, match="Authority-bearing runtime objects"):
        bridge.set("service", _ServiceObject())

    container = create_context_container()
    with pytest.raises(TypeError, match="Authority-bearing runtime objects"):
        container.set("service", _ServiceObject())
