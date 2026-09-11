"""Exercise a standalone app using installed Factory, validation, and secrets."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    import factory_app.eval
    import mozaiksai
    from factory_app.eval import score_bundle
    from mozaiksai.core.secrets import inspect_secret_config, load_secret_contract, resolve_secret
    from mozaiksai.core.validation import validate_app_workspace
    from mozaiksai.core.workflow.context.projection import inject_build_context_projections
    from mozaiksai.resources import (
        resolve_chat_ui_src_root,
        resolve_factory_app_root,
        resolve_web_shell_root,
    )

    installed = args.installed_root.resolve()
    resources = [
        Path(mozaiksai.__file__), Path(factory_app.eval.__file__),
        resolve_factory_app_root(), resolve_web_shell_root(), resolve_chat_ui_src_root(),
    ]
    for resource in resources:
        assert resource is not None and resource.resolve().is_relative_to(installed), resource

    workspace = args.workspace.resolve()
    app = workspace / "app"
    (app / "security").mkdir(parents=True, exist_ok=True)
    (app / "app.json").write_text(json.dumps({"appName": "Package Acceptance"}), encoding="utf-8")
    (app / "security" / "secrets.yaml").write_text(
        "version: 1\nsecrets:\n  - env: PACKAGE_ACCEPTANCE_KEY\n", encoding="utf-8",
    )
    context = workspace / "build_context" / "operator"
    context.mkdir(parents=True, exist_ok=True)
    (context / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "operator", "applies_to_workflows": ["AppGenerator"],
        "assets": [{"path": "catalog.yaml", "kind": "catalog", "projections": [{
            "id": "operator", "records": "capabilities", "recipients": ["AppPlanAgent"],
            "render": "summary", "marker": "CAPABILITY_DIRECTORY_CONTEXT", "heading": "Operator",
        }]}],
    }), encoding="utf-8")
    (context / "catalog.yaml").write_text(yaml.safe_dump({
        "capabilities": [{"id": "operator_example", "label": "Operator Example"}],
    }), encoding="utf-8")
    for key in ("MOZAIKS_BUILD_CONTEXT_PATH", "MOZAIKS_SECRETS_CONFIG_PATH", "MOZAIKS_FACTORY_APP_PATH"):
        os.environ.pop(key, None)
    os.environ.update({
        "MOZAIKS_APP_WORKSPACE_PATH": str(workspace), "PLATFORM_PATH": str(app),
        "PACKAGE_ACCEPTANCE_KEY": "local-acceptance-value",
    })
    os.chdir(workspace)

    factory_root = resolve_factory_app_root()
    assert factory_root is not None
    factory_app_root = factory_root / "app"
    factory_policy = factory_app_root / "security" / "secrets.yaml"
    assert factory_policy.is_file() and factory_policy.resolve().is_relative_to(installed)
    factory_secrets = load_secret_contract(app_root=factory_app_root)
    assert factory_secrets["provider"]["type"] == "env"
    assert inspect_secret_config("MONGO_URI", app_root=factory_app_root).declared
    assert inspect_secret_config("GEMINI_API_KEY", app_root=factory_app_root).declared
    assert not inspect_secret_config("MONGO_URI", app_root=app).declared

    class Agent:
        name = "AppPlanAgent"
        _system_message = "{{CAPABILITY_DIRECTORY_CONTEXT}}"

    agent = Agent()
    inject_build_context_projections(agent, [])
    assert "mozaikspay" in agent._system_message
    assert "Operator Example" in agent._system_message
    assert "{{CAPABILITY_DIRECTORY_CONTEXT}}" not in agent._system_message
    assert validate_app_workspace(app) == []
    assert inspect_secret_config("PACKAGE_ACCEPTANCE_KEY", app_root=app).declared
    assert resolve_secret("PACKAGE_ACCEPTANCE_KEY", app_root=app) == "local-acceptance-value"
    scores = score_bundle(app)
    assert scores and all(item.comment is None or "scorer raised:" not in item.comment for item in scores)
    print("Installed package: Factory + workspace catalogs, app contracts, scoped secret policy, and reference eval passed")


if __name__ == "__main__":
    main()
