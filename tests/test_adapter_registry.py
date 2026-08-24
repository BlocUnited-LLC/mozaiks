"""Declarative adapter registry — resolution, conformance, and fail-closed behavior.

The registry exists so that "adapters are modular" becomes "adapters are
verified against their contract at load time". These tests are therefore
mostly about the failure modes: a reference that does not import, and an
implementation that does not satisfy the port it claims.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

from mozaiksai.core.adapters.registry import (
    AdapterConfigError,
    AdapterConformanceError,
    AdapterResolutionError,
    UnknownAdapterError,
    build_adapter_registry,
    load_adapter_registry,
    load_adapters_config,
    parse_adapters_config,
)

# ── fixtures used as resolution targets ───────────────────────────────────────


@runtime_checkable
class SampleDnsPort(Protocol):
    """Method-only protocol standing in for a real provider port."""

    async def create_record(self, zone: str, name: str, value: str) -> dict: ...

    async def delete_record(self, zone: str, record_id: str) -> dict: ...


class ConformingAdapter:
    async def create_record(self, zone: str, name: str, value: str) -> dict:
        return {"status": "created"}

    async def delete_record(self, zone: str, record_id: str) -> dict:
        return {"status": "deleted"}


class PartialAdapter:
    """Implements only half the port — the realistic drift case."""

    async def create_record(self, zone: str, name: str, value: str) -> dict:
        return {"status": "created"}


_THIS = "tests.test_adapter_registry"


def _config(**overrides) -> dict:
    body = {
        "port": f"{_THIS}:SampleDnsPort",
        "providers": {"good": f"{_THIS}:ConformingAdapter"},
    }
    body.update(overrides)
    return {"schema_version": "mozaiks.adapters.v1", "areas": {"dns": body}}


# ── happy path ────────────────────────────────────────────────────────────────


def test_conforming_adapter_registers() -> None:
    registry = build_adapter_registry(parse_adapters_config(_config()))
    assert registry.areas() == ("dns",)
    assert registry.providers("dns") == ("good",)
    assert registry.get("dns", "good") is ConformingAdapter


def test_active_provider_resolves() -> None:
    registry = build_adapter_registry(parse_adapters_config(_config(active="good")))
    assert registry.active("dns") is ConformingAdapter
    assert registry.active_provider_id("dns") == "good"


def test_active_env_overrides_declared_active() -> None:
    cfg = _config(
        providers={
            "good": f"{_THIS}:ConformingAdapter",
            "other": f"{_THIS}:ConformingAdapter",
        },
        active="good",
        active_env="DNS_ADAPTER",
    )
    registry = build_adapter_registry(
        parse_adapters_config(cfg), env={"DNS_ADAPTER": "other"}
    )
    assert registry.active_provider_id("dns") == "other"


def test_empty_active_env_falls_back_to_declared_active() -> None:
    cfg = _config(active="good", active_env="DNS_ADAPTER")
    registry = build_adapter_registry(parse_adapters_config(cfg), env={"DNS_ADAPTER": ""})
    assert registry.active_provider_id("dns") == "good"


# ── conformance: the reason this exists ───────────────────────────────────────


def test_partial_implementation_is_rejected_naming_the_missing_member() -> None:
    cfg = _config(providers={"partial": f"{_THIS}:PartialAdapter"})
    with pytest.raises(AdapterConformanceError) as exc:
        build_adapter_registry(parse_adapters_config(cfg))
    message = str(exc.value)
    assert "delete_record" in message, "the error must name what is missing"
    assert "partial" in message and "dns" in message


def test_every_declared_provider_is_verified_not_only_the_active_one() -> None:
    """A broken inactive provider is still a defect, and must fail at startup."""
    cfg = _config(
        providers={
            "good": f"{_THIS}:ConformingAdapter",
            "partial": f"{_THIS}:PartialAdapter",
        },
        active="good",
    )
    with pytest.raises(AdapterConformanceError):
        build_adapter_registry(parse_adapters_config(cfg))


def test_unrelated_object_is_rejected() -> None:
    cfg = _config(providers={"nonsense": f"{_THIS}:_config"})
    with pytest.raises(AdapterConformanceError):
        build_adapter_registry(parse_adapters_config(cfg))


# ── resolution failures ───────────────────────────────────────────────────────


def test_missing_module_fails_closed_naming_the_reference() -> None:
    cfg = _config(providers={"ghost": "mozaiksai.does.not.exist:Thing"})
    with pytest.raises(AdapterResolutionError) as exc:
        build_adapter_registry(parse_adapters_config(cfg))
    assert "mozaiksai.does.not.exist" in str(exc.value)


def test_missing_symbol_fails_closed() -> None:
    cfg = _config(providers={"ghost": f"{_THIS}:NoSuchAdapter"})
    with pytest.raises(AdapterResolutionError) as exc:
        build_adapter_registry(parse_adapters_config(cfg))
    assert "NoSuchAdapter" in str(exc.value)


def test_unresolvable_port_fails_closed() -> None:
    cfg = _config(port="mozaiksai.nope:Port")
    with pytest.raises(AdapterResolutionError):
        build_adapter_registry(parse_adapters_config(cfg))


# ── schema validation ─────────────────────────────────────────────────────────


def test_unknown_root_key_is_rejected() -> None:
    raw = _config()
    raw["extra"] = True
    with pytest.raises(AdapterConfigError, match="unknown top-level key"):
        parse_adapters_config(raw)


def test_unknown_area_key_is_rejected() -> None:
    raw = _config(typo_key="oops")
    with pytest.raises(AdapterConfigError, match="unknown key"):
        parse_adapters_config(raw)


def test_wrong_schema_version_is_rejected() -> None:
    raw = _config()
    raw["schema_version"] = "mozaiks.adapters.v0"
    with pytest.raises(AdapterConfigError, match="schema_version"):
        parse_adapters_config(raw)


def test_area_without_port_is_rejected() -> None:
    raw = {"schema_version": "mozaiks.adapters.v1",
           "areas": {"dns": {"providers": {"good": f"{_THIS}:ConformingAdapter"}}}}
    with pytest.raises(AdapterConfigError, match="must declare 'port'"):
        parse_adapters_config(raw)


def test_area_without_providers_is_rejected() -> None:
    raw = {"schema_version": "mozaiks.adapters.v1",
           "areas": {"dns": {"port": f"{_THIS}:SampleDnsPort", "providers": {}}}}
    with pytest.raises(AdapterConfigError, match="non-empty 'providers'"):
        parse_adapters_config(raw)


def test_active_naming_an_undeclared_provider_is_rejected() -> None:
    with pytest.raises(AdapterConfigError, match="not in providers"):
        parse_adapters_config(_config(active="nonexistent"))


def test_active_env_naming_an_undeclared_provider_fails_closed() -> None:
    """Operator typo in an env var must fail loudly, not silently pick nothing."""
    cfg = _config(active="good", active_env="DNS_ADAPTER")
    with pytest.raises(AdapterConfigError, match="not declared"):
        build_adapter_registry(parse_adapters_config(cfg), env={"DNS_ADAPTER": "typo"})


# ── lookup behavior ───────────────────────────────────────────────────────────


def test_unknown_lookup_lists_what_is_registered() -> None:
    registry = build_adapter_registry(parse_adapters_config(_config()))
    with pytest.raises(UnknownAdapterError) as exc:
        registry.get("dns", "missing")
    assert "good" in str(exc.value), "the error should tell the caller what exists"


def test_area_without_active_provider_raises() -> None:
    registry = build_adapter_registry(parse_adapters_config(_config()))
    with pytest.raises(UnknownAdapterError, match="no active provider"):
        registry.active("dns")


# ── file loading ──────────────────────────────────────────────────────────────


def test_absent_file_returns_none(tmp_path: Path) -> None:
    assert load_adapter_registry(tmp_path) is None


def test_loads_from_disk(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "adapters.yaml").write_text(
        textwrap.dedent(
            f"""
            schema_version: mozaiks.adapters.v1
            areas:
              dns:
                port: {_THIS}:SampleDnsPort
                active: good
                providers:
                  good: {_THIS}:ConformingAdapter
            """
        ).strip(),
        encoding="utf-8",
    )
    registry = load_adapter_registry(tmp_path)
    assert registry is not None
    assert registry.active("dns") is ConformingAdapter


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "adapters.yaml").write_text("areas: [unclosed\n", encoding="utf-8")
    with pytest.raises(AdapterConfigError, match="not valid YAML"):
        load_adapters_config(tmp_path)


# ── AppLoader integration: the registry must actually be reachable ────────────


def _write_minimal_app(root: Path, adapters_yaml: str | None) -> None:
    (root / "app.json").write_text('{"appName": "Adapter Test App"}', encoding="utf-8")
    if adapters_yaml is not None:
        (root / "config").mkdir(exist_ok=True)
        (root / "config" / "adapters.yaml").write_text(adapters_yaml, encoding="utf-8")


@pytest.mark.asyncio
async def test_app_loader_exposes_a_verified_registry(tmp_path: Path) -> None:
    """A declared adapter is resolvable from the load result, not just parseable."""
    from mozaiksai.core.runtime.app.loader import AppLoader

    _write_minimal_app(
        tmp_path,
        textwrap.dedent(
            f"""
            schema_version: mozaiks.adapters.v1
            areas:
              dns:
                port: {_THIS}:SampleDnsPort
                active: good
                providers:
                  good: {_THIS}:ConformingAdapter
            """
        ).strip(),
    )
    result = await AppLoader.load(str(tmp_path))
    assert result.adapter_registry is not None
    assert result.adapter_registry.active("dns") is ConformingAdapter


@pytest.mark.asyncio
async def test_app_without_adapters_is_unaffected(tmp_path: Path) -> None:
    """No adapters.yaml means no registry and no behavior change."""
    from mozaiksai.core.runtime.app.loader import AppLoader

    _write_minimal_app(tmp_path, None)
    result = await AppLoader.load(str(tmp_path))
    assert result.adapter_registry is None


@pytest.mark.asyncio
async def test_nonconforming_adapter_fails_app_startup(tmp_path: Path) -> None:
    """A broken declaration must surface at startup, not at first use.

    This is the opposite of the subscriptions loader, which degrades to a
    warning. Silently continuing here would reproduce the swallowed-import
    failure mode this mechanism exists to prevent.
    """
    from mozaiksai.core.runtime.app.loader import AppLoader

    _write_minimal_app(
        tmp_path,
        textwrap.dedent(
            f"""
            schema_version: mozaiks.adapters.v1
            areas:
              dns:
                port: {_THIS}:SampleDnsPort
                providers:
                  partial: {_THIS}:PartialAdapter
            """
        ).strip(),
    )
    with pytest.raises(AdapterConformanceError, match="delete_record"):
        await AppLoader.load(str(tmp_path))
