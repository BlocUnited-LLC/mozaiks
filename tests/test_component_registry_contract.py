from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_component_registry_quiets_same_component_reregistration() -> None:
    source = (REPO_ROOT / "chat-ui/src/registry/componentRegistry.js").read_text()

    assert "existing.component === component" in source
    assert "duplicateRegistrations" in source
    assert source.index("existing.component === component") < source.index("console.warn")


def test_mozaiks_app_keeps_workflow_initializer_stable() -> None:
    source = (REPO_ROOT / "chat-ui/src/app/MozaiksApp.jsx").read_text()

    assert "const moduleInitializer = useCallback(" in source
    assert "workflowInitializer={moduleInitializer}" in source


def test_mozaiks_app_opts_into_react_router_future_flags() -> None:
    source = (REPO_ROOT / "chat-ui/src/app/MozaiksApp.jsx").read_text()

    assert "v7_startTransition: true" in source
    assert "v7_relativeSplatPath: true" in source

