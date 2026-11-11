"""A generated app's refinement harness must land where the Refinement Engine reads it.

AppGenerator emits harness files as app-bundle members (``refinement_harness/...``).
Studio restore, sandbox preview and app validation lay a bundle out as a
canonical workspace through ``app_bundle_workspace_path``. The loader reads the
app harness beside ``app/`` (``<workspace>/refinement_harness/``), as
``docs/architecture/app/refinement-harness.md`` documents, so the mapping must
place harness members at the workspace root, not under ``app/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mozaiksai.control_plane import DEFAULT_REFINEMENT_HARNESS_EXTENDS, load_refinement_harness
from mozaiksai.core.runtime.app.paths import app_bundle_workspace_path


@pytest.mark.parametrize(
    "member",
    [
        "refinement_harness/config/harness.yaml",
        "refinement_harness/config/tools.yaml",
        "refinement_harness/config/policies.yaml",
        "refinement_harness/prompts/coding_refinement_system.yaml",
    ],
)
def test_harness_members_map_to_the_workspace_root(member: str) -> None:
    assert app_bundle_workspace_path(member) == member


@pytest.mark.parametrize(
    ("member", "destination"),
    [
        ("app.json", "app/app.json"),
        ("config/refinement_policy.yaml", "app/config/refinement_policy.yaml"),
        ("modules/notes/module.yaml", "app/modules/notes/module.yaml"),
        ("requirements.txt", "requirements.txt"),
        ("Dockerfile", "Dockerfile"),
        ("workflows/Triage/agents.yaml", "workflows/Triage/agents.yaml"),
        ("build_context/Pack/context.yaml", "build_context/Pack/context.yaml"),
    ],
)
def test_other_bundle_members_keep_their_placement(member: str, destination: str) -> None:
    assert app_bundle_workspace_path(member) == destination


def test_generated_harness_override_reaches_the_refinement_engine(tmp_path: Path) -> None:
    bundle = {
        "app.json": json.dumps({"appId": "notes", "name": "Notes"}),
        "config/refinement_policy.yaml": "schema_version: mozaiks.refinement.policy.v1\nenabled: true\n",
        "refinement_harness/config/harness.yaml": yaml.safe_dump(
            {
                "schema_version": "mozaiks.refinement_harness.v1",
                "extends": DEFAULT_REFINEMENT_HARNESS_EXTENDS,
                "overrides": {},
            },
            sort_keys=False,
        ),
        "refinement_harness/config/policies.yaml": yaml.safe_dump(
            {
                "schema_version": "mozaiks.refinement_harness.policies.v1",
                "scope": {"max_selected_paths": 5},
            },
            sort_keys=False,
        ),
    }
    workspace = tmp_path / "workspace"
    for member, content in bundle.items():
        destination = workspace / app_bundle_workspace_path(member)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    pack = load_refinement_harness(app_root=workspace / "app")

    assert pack.path == (workspace / "refinement_harness").resolve()
    assert pack.policies.scope.max_selected_paths == 5
