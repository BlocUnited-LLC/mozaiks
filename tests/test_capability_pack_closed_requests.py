"""Canonical split-pack action request contracts satisfy the closed profile.

Every workspace_handler_split capability-pack module ships action
``input_schema`` documents that import under the #484 closed-contract profile
(``mozaiks.closed_contract_profile.v1``): ``additionalProperties: false`` at
every object level, only profile keywords, and no open map/dictionary nodes.

Beyond schema import, this suite proves dispatch behavior with the real
runtime validator and that handlers still receive the same semantic values:
representative valid requests validate, unknown extras reject, missing
required properties reject, optional omissions pass, redesigned metadata /
raw_event shapes fold into the exact stored record shapes used before the
contracts closed, and every schema ``default`` removed by the closure still
exists as the handler/service code default.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"

#: Every canonical workspace_handler_split module (pack directory, module id).
SPLIT_MODULES: tuple[tuple[str, str], ...] = (
    ("commerce", "commerce"),
    ("entitlement_dispatch", "entitlement_dispatch"),
    ("files", "files"),
    ("messaging", "messages"),
    ("mozaikspay", "billing_portal"),
    ("notifications", "notification_settings"),
    ("social", "activity_feed"),
    ("social", "friends"),
    ("social", "user_posts"),
    ("support", "support"),
)

EXPECTED_ACTION_COUNT = 63


def _module_yaml(pack: str, module: str) -> dict[str, Any]:
    path = BUILD_CONTEXT / pack / "templates" / "modules" / module / "module.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _all_actions() -> list[tuple[str, str, dict[str, Any]]]:
    actions: list[tuple[str, str, dict[str, Any]]] = []
    for pack, module in SPLIT_MODULES:
        for action in _module_yaml(pack, module).get("actions") or []:
            actions.append((pack, module, action))
    return actions


ACTIONS = _all_actions()
ACTION_IDS = [f"{pack}/{module}:{action['id']}" for pack, module, action in ACTIONS]


def _split_backends_present() -> None:
    for pack, module in SPLIT_MODULES:
        base = BUILD_CONTEXT / pack / "templates" / "modules" / module / "backend"
        assert (base / "base_handler.py").is_file(), f"{pack}/{module} lost its handler split"


def test_census_is_complete() -> None:
    """The split-pack corpus is exactly the audited 10 modules / 63 actions."""
    _split_backends_present()
    assert len(SPLIT_MODULES) == 10
    assert len(ACTIONS) == EXPECTED_ACTION_COUNT


# ---------------------------------------------------------------------------
# Closed-profile import and explicit closure at every object level
# ---------------------------------------------------------------------------


def _object_nodes(schema: Any, path: str = "$"):
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        yield path, schema
        for name, child in (schema.get("properties") or {}).items():
            yield from _object_nodes(child, f"{path}.{name}")
    elif schema.get("type") == "array":
        yield from _object_nodes(schema.get("items"), f"{path}[]")


@pytest.mark.parametrize("pack,module,action", ACTIONS, ids=ACTION_IDS)
def test_action_request_imports_closed_contract(pack: str, module: str, action: dict) -> None:
    import_closed_contract_schema(action.get("input_schema") or {})


@pytest.mark.parametrize("pack,module,action", ACTIONS, ids=ACTION_IDS)
def test_every_object_level_is_explicitly_closed(pack: str, module: str, action: dict) -> None:
    schema = action.get("input_schema") or {}
    nodes = list(_object_nodes(schema))
    assert nodes, f"{action['id']} input_schema must be an object contract"
    for node_path, node in nodes:
        assert node.get("additionalProperties") is False, (
            f"{pack}/{module}:{action['id']} object node {node_path} is not explicitly closed"
        )


# ---------------------------------------------------------------------------
# Dispatch behavior with the real runtime validator
# ---------------------------------------------------------------------------


def _representative_value(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    enum = schema.get("enum")
    if enum:
        return enum[0]
    if kind == "string":
        return "value"
    if kind == "integer":
        return 3
    if kind == "number":
        return 1.5
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    if kind == "array":
        return [_representative_value(schema["items"])]
    if kind == "object":
        return _representative_request(schema, include_optional=False)
    raise AssertionError(f"unsupported representative type {kind!r}")


def _representative_request(schema: dict[str, Any], *, include_optional: bool) -> dict[str, Any]:
    request: dict[str, Any] = {}
    required = set(schema.get("required") or [])
    for name, child in (schema.get("properties") or {}).items():
        if name in required or include_optional:
            request[name] = _representative_value(child)
    return request


@pytest.mark.parametrize("pack,module,action", ACTIONS, ids=ACTION_IDS)
def test_dispatch_validation_behavior(pack: str, module: str, action: dict) -> None:
    schema = action.get("input_schema") or {}

    # A representative request carrying every property still validates.
    full = _representative_request(schema, include_optional=True)
    assert validate_json_schema(full, schema) is None

    # Omitting every optional property still validates.
    required_only = _representative_request(schema, include_optional=False)
    assert validate_json_schema(required_only, schema) is None

    # An unknown extra property now rejects.
    assert validate_json_schema({**required_only, "unknown_extra": 1}, schema) is not None

    # Dropping any one required property rejects.
    for name in schema.get("required") or []:
        broken = {key: value for key, value in required_only.items() if key != name}
        assert validate_json_schema(broken, schema) is not None, (
            f"{pack}/{module}:{action['id']} accepted a request missing required {name!r}"
        )


# ---------------------------------------------------------------------------
# Backend template loading (same seam as test_build_context_messaging_service)
# ---------------------------------------------------------------------------


def _load_backend(pack: str, module: str, names: list[str]) -> dict[str, ModuleType]:
    backend = BUILD_CONTEXT / pack / "templates" / "modules" / module / "backend"
    package = f"tests.closed_requests_{pack}_{module}_backend"
    if package not in sys.modules:
        package_module = ModuleType(package)
        package_module.__path__ = [str(backend)]
        sys.modules[package] = package_module
    loaded: dict[str, ModuleType] = {}
    for name in names:
        qualified = f"{package}.{name}"
        if qualified in sys.modules:
            loaded[name] = sys.modules[qualified]
            continue
        spec = importlib.util.spec_from_file_location(qualified, backend / f"{name}.py")
        loaded_module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = loaded_module
        spec.loader.exec_module(loaded_module)
        loaded[name] = loaded_module
    return loaded


def _ctx(**extra: Any) -> SimpleNamespace:
    async def emit(event_type: str, payload: dict) -> None:
        return None

    defaults: dict[str, Any] = {
        "user_id": "user_1",
        "app_id": "app_1",
        "workspace_id": "ws_1",
        "tenant_id": "tenant_1",
        "emit": emit,
    }
    defaults.update(extra)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Runtime semantic preservation for the redesigned request shapes
# ---------------------------------------------------------------------------


async def test_entitlement_metadata_entries_store_the_same_map() -> None:
    modules = _load_backend("entitlement_dispatch", "entitlement_dispatch", ["repo", "schemas", "service"])
    service = modules["service"].EntitlementDispatchService()

    captured: dict[str, Any] = {}

    class _FakeRepo:
        async def activate(self, ctx, **kwargs):
            captured.update(kwargs)

    service._repo = _FakeRepo()
    result = await service.activate_subscription(
        _ctx(),
        user_id="user_1",
        plan_id="pro",
        metadata=[
            {"key": "provider_reference", "value": "ref_9"},
            {"key": "source", "value": "checkout"},
        ],
    )
    assert result == {"activated": True, "plan_id": "pro"}
    assert captured["metadata"] == {"provider_reference": "ref_9", "source": "checkout"}


async def test_files_metadata_entries_store_the_same_map(monkeypatch) -> None:
    modules = _load_backend("files", "files", ["schemas", "policy", "repo", "service"])
    service_module = modules["service"]
    captured: dict[str, Any] = {}

    async def fake_register_file(ctx, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service_module, "register_file", fake_register_file)
    service = service_module.FilesService()
    result = await service.upload_file(
        _ctx(),
        filename="a.txt",
        mime_type="text/plain",
        size_bytes=4,
        storage_url="prov://a",
        metadata=[{"key": "camera", "value": "front"}],
    )
    assert result["success"] is True
    assert captured["metadata"] == {"camera": "front"}
    assert captured["is_public"] is False


async def test_messaging_metadata_entries_store_the_same_map() -> None:
    modules = _load_backend("messaging", "messages", ["schemas", "policy", "repo", "service"])
    inserted: list[dict[str, Any]] = []

    class _FakeThreads:
        async def insert(self, ctx, *, record):
            inserted.append(dict(record))

    service = modules["service"].MessageService(
        threads=_FakeThreads(), messages=object(), reads=object()
    )
    result = await service.create_thread(
        _ctx(),
        title="Support",
        thread_type="support",
        related_type="support.request",
        related_id="req_1",
        metadata=[{"key": "request_id", "value": "req_1"}],
    )
    assert result["success"] is True
    assert inserted[0]["metadata"] == {"request_id": "req_1"}
    # Omitted thread defaults still apply from code.
    assert inserted[0]["scope_type"] == "app"


async def test_commerce_raw_event_type_still_recorded() -> None:
    modules = _load_backend("commerce", "commerce", ["schemas", "policy", "repo", "service"])
    order = {"order_id": "ord_1", "status": "pending_payment", "payment": {}}
    updates_seen: dict[str, Any] = {}

    class _FakeOrders:
        async def find_by_provider_reference(self, ctx, *, provider_reference):
            return None

        async def get(self, ctx, *, order_id):
            return dict(order)

        async def update(self, ctx, *, order_id, updates):
            updates_seen.update(updates)
            return {**order, **updates}

    class _FakeCheckoutRequests:
        async def update_status(self, ctx, *, order_id, updates):
            return None

    service = modules["service"].CommerceService(
        products=object(),
        carts=object(),
        orders=_FakeOrders(),
        checkout_requests=_FakeCheckoutRequests(),
    )
    result = await service.record_checkout_result(
        _ctx(),
        order_id="ord_1",
        status="failed",
        provider="mozaikspay",
        provider_reference="ref_1",
        raw_event={"type": "checkout.session.expired"},
    )
    assert result["success"] is True
    assert updates_seen["payment_event"]["event_type"] == "checkout.session.expired"


async def test_user_posts_body_length_bound_is_preserved() -> None:
    modules = _load_backend("social", "user_posts", ["schemas", "policy", "repo", "service"])
    service = modules["service"].UserPostsService()

    created: list[dict[str, Any]] = []

    class _FakePosts:
        async def create(self, ctx, doc):
            created.append(dict(doc))

    service._posts = _FakePosts()

    rejected = await service.create_post(_ctx(), body="x" * 5001)
    assert rejected["success"] is False
    assert "5000" in rejected["error"]
    assert created == []

    accepted = await service.create_post(_ctx(), body="x" * 5000)
    assert accepted["success"] is True
    assert len(created) == 1

    over_comment = await service.add_comment(_ctx(), post_id="pst_1", body="y" * 2001)
    assert over_comment["success"] is False
    assert "2000" in over_comment["error"]


# ---------------------------------------------------------------------------
# Every schema default removed by the closure still exists in code
# ---------------------------------------------------------------------------


def _signature_defaults(pack: str, module: str, file_name: str, function: str) -> dict[str, Any]:
    path = BUILD_CONTEXT / pack / "templates" / "modules" / module / "backend" / file_name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == function:
            args = node.args
            defaults: dict[str, Any] = {}
            kwonly = args.kwonlyargs
            for arg, default in zip(kwonly, args.kw_defaults, strict=True):
                if default is not None:
                    defaults[arg.arg] = ast.literal_eval(default)
            positional = args.args
            for arg, default in zip(
                positional[len(positional) - len(args.defaults) :], args.defaults, strict=True
            ):
                defaults[arg.arg] = ast.literal_eval(default)
            return defaults
    raise AssertionError(f"{pack}/{module} {file_name} lacks {function}")


@pytest.mark.parametrize(
    "pack,module,file_name,function,expected",
    [
        ("files", "files", "service.py", "upload_file", {"is_public": False}),
        ("messaging", "messages", "service.py", "create_thread", {"thread_type": "group", "scope_type": "app"}),
        ("messaging", "messages", "service.py", "send_message", {"message_type": "text"}),
        ("mozaikspay", "billing_portal", "service.py", "get_usage_status", {"limit": 500}),
        ("mozaikspay", "billing_portal", "service.py", "get_token_status", {"limit": 500}),
        ("mozaikspay", "billing_portal", "service.py", "start_subscription_checkout", {"customer_email": None}),
        ("support", "support", "base_handler.py", "create_support_request", {"severity": "low"}),
        (
            "support",
            "support",
            "base_handler.py",
            "list_support_requests",
            {"status": "open", "scope": "user", "limit": 50},
        ),
        ("entitlement_dispatch", "entitlement_dispatch", "service.py", "activate_subscription", {"activated_at": None, "metadata": None}),
        ("entitlement_dispatch", "entitlement_dispatch", "service.py", "deactivate_subscription", {"plan_id": None}),
    ],
)
def test_removed_schema_defaults_survive_in_code(
    pack: str, module: str, file_name: str, function: str, expected: dict[str, Any]
) -> None:
    defaults = _signature_defaults(pack, module, file_name, function)
    for name, value in expected.items():
        assert defaults.get(name) == value, (
            f"{pack}/{module} {function} lost the code default for {name!r}"
        )
