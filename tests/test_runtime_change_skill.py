from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_runtime_change_skill_exists_and_names_runtime_platform_anchors() -> None:
    skill = _read(".claude/skills/runtime-change/SKILL.md")

    assert "mozaiksai/hosts/runtime.py" in skill
    assert "mozaiksai/hosts/platform.py" in skill
    assert "mozaiksai/core/runtime/app/module_loader.py" in skill
    assert "mozaiksai/core/runtime/composition/module_executor.py" in skill
    assert "mozaiksai/core/runtime/composition/module_event_router.py" in skill
    assert "mozaiksai/core/runtime/composition/module_context.py" in skill
    assert "mozaiksai/core/runtime/composition/extensions.py" in skill


def test_runtime_change_skill_covers_guardrails_and_focused_tests() -> None:
    skill = _read(".claude/skills/runtime-change/SKILL.md")

    assert "mozaiksai/core/auth/**" in skill
    assert "mozaiksai/core/transport/**" in skill
    assert "mozaiksai/core/runtime/persistence/**" in skill
    assert "runtime_extensions.yaml" in skill
    assert "Do not put hosted-only product logic into runtime or platform code." in skill
    assert "Run the narrowest area test first." in skill
    assert "tests/test_module_loader_contracts.py" in skill
    assert "tests/test_module_runtime_extensions.py" in skill
    assert "tests/test_runtime_persistence_module_injection.py" in skill
    assert "tests/test_runtime_websocket_contract.py" in skill
    assert "tests/test_auth_oidc_discovery.py" in skill
    assert "tests/test_platform_ai_config_resolution.py" in skill
    assert "OSS Change Impact" in skill


def test_runtime_change_skill_is_routed_from_skill_index_and_quickstart() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "| Runtime/platform change | `runtime-change` |" in skills
    assert "planned `runtime-change`" not in skills
    assert "Runtime or platform change: use `runtime-change`" in quickstart


def test_runtime_change_guidance_stays_public_and_provider_neutral() -> None:
    for relative_path in [
        ".claude/skills/runtime-change/SKILL.md",
        ".claude/skills/README.md",
        "CONTRIBUTING.md",
    ]:
        text = _read(relative_path)
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path
        assert "mozaiks-app" not in text, relative_path

    skill = _read(".claude/skills/runtime-change/SKILL.md")
    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in skill, forbidden