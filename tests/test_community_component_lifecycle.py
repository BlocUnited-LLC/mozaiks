"""Community Component local install and upgrade lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path
from shutil import copytree

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "community_packs"
GREETINGS = FIXTURES / "greetings"
FAREWELL = FIXTURES / "farewell"


def _install(pack: Path, build_context_root: Path):
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        install_verified_local_pack,
    )

    return install_verified_local_pack(pack_source_path=pack, build_context_root=build_context_root)


def _materialized_files(pack: Path, pack_id: str) -> dict[str, str]:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )

    return {
        item["filename"]: item["content"]
        for item in resolve_managed_capability_templates(
            [{"id": pack_id, "pack_id": pack_id, "pack_source_path": str(pack)}]
        )
    }


def _write_app_root_from_pack(app_root: Path, pack: Path, pack_id: str) -> None:
    for filename, content in _materialized_files(pack, pack_id).items():
        target = app_root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _greetings_v2(tmp_path: Path, *, add_extra: bool = False, remove_init: bool = False) -> Path:
    v2 = tmp_path / "greetings_v2"
    copytree(GREETINGS, v2)
    context = v2 / "context.yaml"
    context.write_text(
        context.read_text(encoding="utf-8").replace('version: "0.1.0"', 'version: "0.2.0"'),
        encoding="utf-8",
    )
    service = v2 / "templates" / "modules" / "greetings" / "backend" / "service.py"
    service.write_text(
        service.read_text(encoding="utf-8").replace("fixture community pack", "fixture community pack v2"),
        encoding="utf-8",
    )
    if add_extra:
        (service.parent / "extra.py").write_text('"""v2 extra file."""\n', encoding="utf-8")
    if remove_init:
        (service.parent / "__init__.py").unlink()
    return v2


def test_install_verified_local_pack_writes_path_free_canonical_state(tmp_path: Path) -> None:
    build_context_root = tmp_path / "build_context"

    result = _install(GREETINGS, build_context_root)

    assert result.status == "installed"
    state_raw = Path(result.installed_state_path).read_text(encoding="utf-8")
    state = json.loads(state_raw)
    assert state["schema_version"] == "mozaiks.installed_components.v1"
    assert state["components"][0]["pack_id"] == "greetings"
    assert state["components"][0]["version"] == "0.1.0"
    assert state["components"][0]["digest"].startswith("sha256:")
    assert str(GREETINGS) not in state_raw
    sources = json.loads(Path(result.source_state_path).read_text(encoding="utf-8"))
    assert sources["schema_version"] == "mozaiks.installed_component_sources.v1"
    assert sources["sources"][0]["local_source_path"] == str(GREETINGS.resolve())


def test_tampered_local_pack_install_fails_before_state_is_written(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
    )

    bad_pack = tmp_path / "bad_greetings"
    copytree(GREETINGS, bad_pack)
    context = bad_pack / "context.yaml"
    context.write_text(context.read_text(encoding="utf-8") + "\ninjected_prompt: bad\n", encoding="utf-8")

    with pytest.raises(ManagedCapabilityTemplateError):
        _install(bad_pack, tmp_path / "build_context")

    assert not (tmp_path / "build_context" / ".mozaiks" / "installed_components.json").exists()


def test_dependency_missing_install_fails(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackDependencyError,
    )

    with pytest.raises(PackDependencyError):
        _install(FAREWELL, tmp_path / "build_context")


def test_same_exact_pack_reinstall_is_idempotent(tmp_path: Path) -> None:
    build_context_root = tmp_path / "build_context"
    first = _install(GREETINGS, build_context_root)
    before = Path(first.installed_state_path).read_text(encoding="utf-8")

    second = _install(GREETINGS, build_context_root)
    after = Path(second.installed_state_path).read_text(encoding="utf-8")

    assert second.status == "unchanged"
    assert before == after


def test_malformed_installed_state_fails_instead_of_dropping_entry(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_state import (
        InstalledComponentStateError,
        load_installed_components,
    )

    state_path = tmp_path / "build_context" / ".mozaiks" / "installed_components.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "schema_version": "mozaiks.installed_components.v1",
            "components": ["not-an-object"],
        }),
        encoding="utf-8",
    )

    with pytest.raises(InstalledComponentStateError, match="components\\[0\\] must be an object"):
        load_installed_components(tmp_path / "build_context")


def test_two_compatible_packs_install_when_dependency_is_present(tmp_path: Path) -> None:
    build_context_root = tmp_path / "build_context"

    _install(GREETINGS, build_context_root)
    result = _install(FAREWELL, build_context_root)

    assert result.status == "installed"
    state = json.loads((build_context_root / ".mozaiks" / "installed_components.json").read_text(encoding="utf-8"))
    assert [component["pack_id"] for component in state["components"]] == ["farewell", "greetings"]


def test_installed_pack_is_not_materialized_until_explicitly_selected(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )

    build_context_root = tmp_path / "build_context"
    _install(GREETINGS, build_context_root)

    assert resolve_managed_capability_templates([], context_variables={"build_context_root": str(build_context_root)}) == []
    files = resolve_managed_capability_templates(
        [{"capability_pack_id": "greetings"}],
        context_variables={"build_context_root": str(build_context_root)},
    )

    assert any(item["filename"] == "modules/greetings/module.yaml" for item in files)


def test_upgrade_plan_reports_deterministic_file_changes(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        plan_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    _install(GREETINGS, build_context_root)
    candidate = _greetings_v2(tmp_path, remove_init=True)

    plan = plan_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=candidate,
    )

    assert plan.status == "ready"
    assert plan.identity_match is True
    assert plan.from_version == "0.1.0"
    assert plan.to_version == "0.2.0"
    assert "modules/greetings/backend/service.py" in plan.changed_files
    assert "modules/greetings/backend/__init__.py" in plan.removed_files


def test_upgrade_plan_detects_workspace_owned_file_collision(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        plan_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    app_root = tmp_path / "app"
    _install(GREETINGS, build_context_root)
    candidate = _greetings_v2(tmp_path, add_extra=True)
    target = app_root / "modules" / "greetings" / "backend" / "extra.py"
    target.parent.mkdir(parents=True)
    target.write_text("custom\n", encoding="utf-8")

    plan = plan_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=candidate,
        workspace_app_root=app_root,
    )

    assert {"path": "modules/greetings/backend/extra.py", "kind": "workspace_owned_file"} in plan.potential_conflicts


def test_upgrade_plan_detects_other_pack_ownership_collision(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        plan_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    app_root = tmp_path / "app"
    _install(GREETINGS, build_context_root)
    candidate = _greetings_v2(tmp_path)
    provenance = {
        "schema_version": "mozaiks.pack_provenance.v1",
        "framework_version": "test",
        "generated_at": "2026-08-13T00:00:00+00:00",
        "packs": [
            {
                "pack_id": "other_pack",
                "version": "1.0.0",
                "source": "local",
                "digest": "sha256:" + "a" * 64,
                "materialized_owned_files": [
                    {"path": "modules/greetings/backend/service.py", "owner": "templates"}
                ],
            }
        ],
    }
    target = app_root / ".mozaiks" / "pack_provenance.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(provenance), encoding="utf-8")

    plan = plan_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=candidate,
        workspace_app_root=app_root,
    )

    assert any(conflict["kind"] == "owned_by_other_pack" for conflict in plan.potential_conflicts)


def test_upgrade_plan_detects_local_modification_conflict(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        plan_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    app_root = tmp_path / "app"
    _install(GREETINGS, build_context_root)
    _write_app_root_from_pack(app_root, GREETINGS, "greetings")
    service = app_root / "modules" / "greetings" / "backend" / "service.py"
    service.write_text(service.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")

    plan = plan_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=_greetings_v2(tmp_path),
        workspace_app_root=app_root,
    )

    assert any(conflict["kind"] == "locally_modified_owned_file" for conflict in plan.potential_conflicts)


def test_upgrade_plan_blocks_removed_file_when_provenance_does_not_own_it(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        plan_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    app_root = tmp_path / "app"
    _install(GREETINGS, build_context_root)
    _write_app_root_from_pack(app_root, GREETINGS, "greetings")
    provenance_path = app_root / ".mozaiks" / "pack_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["packs"][0]["materialized_owned_files"] = [
        file_entry
        for file_entry in provenance["packs"][0]["materialized_owned_files"]
        if file_entry["path"] != "modules/greetings/backend/__init__.py"
    ]
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    plan = plan_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=_greetings_v2(tmp_path, remove_init=True),
        workspace_app_root=app_root,
    )

    assert {"path": "modules/greetings/backend/__init__.py", "kind": "not_owned_by_installed_pack"} in plan.potential_conflicts


def test_safe_upgrade_apply_updates_state_and_provenance(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        apply_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    app_root = tmp_path / "app"
    _install(GREETINGS, build_context_root)
    _write_app_root_from_pack(app_root, GREETINGS, "greetings")
    candidate = _greetings_v2(tmp_path, remove_init=True)

    plan = apply_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=candidate,
        workspace_app_root=app_root,
    )

    assert plan.status == "ready"
    assert not (app_root / "modules" / "greetings" / "backend" / "__init__.py").exists()
    state = json.loads((build_context_root / ".mozaiks" / "installed_components.json").read_text(encoding="utf-8"))
    assert state["components"][0]["version"] == "0.2.0"
    provenance = json.loads((app_root / ".mozaiks" / "pack_provenance.json").read_text(encoding="utf-8"))
    assert provenance["packs"][0]["version"] == "0.2.0"


def test_safe_upgrade_apply_preserves_other_pack_provenance(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        apply_component_upgrade,
    )

    build_context_root = tmp_path / "build_context"
    app_root = tmp_path / "app"
    _install(GREETINGS, build_context_root)
    _write_app_root_from_pack(app_root, GREETINGS, "greetings")
    provenance_path = app_root / ".mozaiks" / "pack_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["packs"].append({
        "pack_id": "other_pack",
        "version": "1.0.0",
        "source": "local",
        "digest": "sha256:" + "a" * 64,
        "materialized_owned_files": [
            {"path": "modules/other/module.yaml", "owner": "templates"}
        ],
    })
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    apply_component_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=_greetings_v2(tmp_path),
        workspace_app_root=app_root,
    )

    updated = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert {pack["pack_id"] for pack in updated["packs"]} == {"greetings", "other_pack"}
    assert next(pack for pack in updated["packs"] if pack["pack_id"] == "greetings")["version"] == "0.2.0"
