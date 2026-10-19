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
    """The pack's files plus the shared UI roots the canonical composer builds from its contribution."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
        verify_pack_integrity,
    )
    from mozaiksai.core.workflow.generator_support.ui_registry_composition import (
        compose_ui_registry,
    )

    # An external source is selected with the version and digest its bytes verify to.
    metadata = verify_pack_integrity(pack, pack_id)
    selected = {"id": pack_id, "pack_id": pack_id, "pack_source_path": str(pack),
                "version": metadata["version"], "digest": metadata["digest"]}
    contributions: list[dict] = []
    files = {
        item["filename"]: item["content"]
        for item in resolve_managed_capability_templates([selected], ui_contributions=contributions)
    }
    files.update(compose_ui_registry(contributions, files))
    return files


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


def test_upgrade_refuses_an_installed_source_changed_in_place(tmp_path: Path) -> None:
    """The comparison base must be what the app was materialized from: an installed source edited in place
    no longer matches its pinned state, so no upgrade is planned or applied from it, and nothing changes."""
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        CommunityComponentLifecycleError,
        apply_component_upgrade,
        plan_component_upgrade,
    )

    source = tmp_path / "source" / "greetings"
    copytree(GREETINGS, source)
    build_context_root = tmp_path / "build_context"
    _install(source, build_context_root)
    state = build_context_root / "installed_components.json"
    before = state.read_bytes() if state.exists() else None
    manifest = source / "context.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('version: "0.1.0"', 'version: "0.2.0"'), encoding="utf-8",
    )
    service = next(source.rglob("service.py"))
    service.write_text(service.read_text(encoding="utf-8") + "\n# changed in place\n", encoding="utf-8")
    app_root = tmp_path / "app"
    app_root.mkdir()
    with pytest.raises(CommunityComponentLifecycleError, match="no longer matches its pinned state.*now version 0.2.0"):
        plan_component_upgrade(build_context_root=build_context_root, candidate_pack_source_path=source)
    with pytest.raises(CommunityComponentLifecycleError, match="no longer matches its pinned state"):
        apply_component_upgrade(
            build_context_root=build_context_root, candidate_pack_source_path=source, workspace_app_root=app_root,
        )
    assert list(app_root.iterdir()) == []
    assert (state.read_bytes() if state.exists() else None) == before
    # The same version with other bytes (an in-place edit, or a re-checkout with other line endings) is named so.
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('version: "0.2.0"', 'version: "0.1.0"'), encoding="utf-8",
    )
    with pytest.raises(CommunityComponentLifecycleError, match="its bytes changed since installation"):
        plan_component_upgrade(build_context_root=build_context_root, candidate_pack_source_path=source)


_UNCOVERED = "does not cover its contract.yaml"


def _uncovered_pin(tmp_path: Path, case: str):
    """A greetings installation whose pin does not cover the contract.yaml its source holds now.

    The earlier cases reproduce a 0.2.0 install of a pack whose contract.yaml is not a declared asset (the
    recorded digest leaves it out), then edit the contract in place as reported: forbidden outputs removed,
    provided capabilities added, or a dependency added (the one contract field the pin records).
    ``contract_added`` installs a pack with no contract.yaml under the current rule and adds one later.
    """
    import json as _json

    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        pre_contract_rule_digest,
    )

    source = tmp_path / "source" / "greetings"
    copytree(GREETINGS, source)
    manifest = source / "context.yaml"
    declared = "  - path: contract.yaml\n    kind: contract\n"
    text = manifest.read_text(encoding="utf-8")
    assert declared in text
    manifest.write_text(text.replace(declared, ""), encoding="utf-8")
    contract = source / "contract.yaml"
    original_contract = contract.read_text(encoding="utf-8")
    build_context_root = tmp_path / "build_context"
    if case == "contract_added":
        contract.unlink()
        installed = _install(source, build_context_root)
        contract.write_text(original_contract, encoding="utf-8")
    else:
        installed = _install(source, build_context_root)
        # The pin 0.2.0 recorded: the digest without the undeclared contract.yaml.
        state = Path(installed.installed_state_path)
        recorded = _json.loads(state.read_text(encoding="utf-8"))
        for component in recorded["components"]:
            component["digest"] = pre_contract_rule_digest(source, "greetings")
        state.write_text(_json.dumps(recorded), encoding="utf-8")
        if case == "forbidden_outputs_removed":
            assert "forbidden_outputs:" in original_contract
            kept = original_contract.split("forbidden_outputs:")[0]
            rest = original_contract.split("forbidden_outputs:")[1].split("\n\n", 1)
            contract.write_text(kept + (rest[1] if len(rest) > 1 else ""), encoding="utf-8")
        elif case == "capabilities_added":
            contract.write_text(original_contract + "\nprovides_capabilities:\n  - subscription_write_path\n", encoding="utf-8")
        elif case == "dependency_added":
            contract.write_text(
                original_contract + "\nrequires:\n  capabilities:\n    - capability_id: greetings.say_hello\n"
                "      reason: greetings check a plan\n",
                encoding="utf-8",
            )
    return source, build_context_root, Path(installed.installed_state_path)


@pytest.mark.parametrize("case", [
    "unchanged", "forbidden_outputs_removed", "capabilities_added", "contract_added", "dependency_added",
])
def test_a_pin_that_never_covered_contract_yaml_adopts_no_contract_and_names_the_cause(tmp_path: Path, case: str) -> None:
    """A pin whose digest left out the contract.yaml cannot show which contract the pack was installed with, so no
    step adopts the source's current contract over it: the upgrade plan, a repeated installation and installed
    resolution all refuse with the same cause, compare the recorded metadata that exists, claim nothing about
    the unrecorded contract, name the installation boundary that pins a whole pack, and change nothing."""
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        CommunityComponentLifecycleError,
        plan_component_upgrade,
        resolve_installed_component_descriptors,
    )
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackIntegrityError,
    )

    source, build_context_root, state = _uncovered_pin(tmp_path, case)
    before = state.read_bytes()
    candidate = tmp_path / "candidate" / "greetings"
    copytree(source, candidate)
    candidate_manifest = candidate / "context.yaml"
    candidate_manifest.write_text(
        candidate_manifest.read_text(encoding="utf-8").replace('version: "0.1.0"', 'version: "0.2.0"'), encoding="utf-8",
    )
    refusals = []
    with pytest.raises(CommunityComponentLifecycleError, match=_UNCOVERED) as planned:
        plan_component_upgrade(build_context_root=build_context_root, candidate_pack_source_path=candidate)
    refusals.append(str(planned.value))
    with pytest.raises(CommunityComponentLifecycleError, match=_UNCOVERED) as reinstalled:
        _install(source, build_context_root)
    refusals.append(str(reinstalled.value))
    with pytest.raises(PackIntegrityError, match=_UNCOVERED) as resolved:
        resolve_installed_component_descriptors(build_context_root=build_context_root, selected_pack_ids=["greetings"])
    refusals.append(str(resolved.value))
    for message in refusals:
        assert "cannot be verified" in message and "does not show that the contract" in message, message
        assert "install the pack from its verified source into a build context that does not hold this pin" in message
        assert "unchanged source" not in message and "its source is unchanged" not in message
        assert message.endswith("Nothing was changed.")
    for message in refusals:
        assert "the version, the declared files and the capabilities they declare match the source" in message
        if case == "dependency_added":
            assert "recorded from the contract differ" in message and "greetings.say_hello" in message, message
        else:
            assert "the dependencies it recorded from the contract match" in message, message
    assert state.read_bytes() == before


def test_a_fresh_installation_pins_the_current_contract(tmp_path: Path) -> None:
    """The boundary the refusal names: installing the verified source into a build context without the old pin
    verifies and pins the whole pack, contract.yaml included."""
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        resolve_installed_component_descriptors,
    )
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        verify_pack_integrity,
    )

    source, _, _ = _uncovered_pin(tmp_path, "forbidden_outputs_removed")
    fresh = tmp_path / "fresh_build_context"
    result = _install(source, fresh)
    assert (result.status, result.digest) == ("installed", verify_pack_integrity(source, "greetings")["digest"])
    [descriptor] = resolve_installed_component_descriptors(build_context_root=fresh, selected_pack_ids=["greetings"])
    assert descriptor["digest"] == result.digest


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
