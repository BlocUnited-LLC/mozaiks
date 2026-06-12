from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]


def _read_yaml(relative_path: str) -> dict:
    return yaml.safe_load((WORKSPACE / relative_path).read_text(encoding="utf-8")) or {}


def _read_text(relative_path: str) -> str:
    return (WORKSPACE / relative_path).read_text(encoding="utf-8")


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_theme_capture_uses_canonical_context_and_handoffs() -> None:
    context_vars = _read_yaml("factory_app/workflows/ThemeCapture/context_variables.yaml")
    handoffs = _read_yaml("factory_app/workflows/ThemeCapture/transition_graph.yaml")
    tools = _read_yaml("factory_app/workflows/ThemeCapture/tools.yaml")

    assert "definitions" in context_vars
    assert "agents" in context_vars

    interview = context_vars["definitions"]["interview_complete"]
    analysis = context_vars["definitions"]["analysis_complete"]

    interview_trigger = interview["source"]["triggers"][0]
    analysis_trigger = analysis["source"]["triggers"][0]

    assert interview_trigger["agent"] == "ThemeInterviewAgent"
    assert interview_trigger["match"]["equals"] == "NEXT"
    assert analysis_trigger["agent"] == "ThemeAnalysisAgent"
    assert analysis_trigger["match"]["equals"] == "NEXT"

    route_pairs = {
        (item["source_agent"], item["target_agent"])
        for item in handoffs.get("transition_rules", [])
    }
    assert ("ThemeInterviewAgent", "ThemeAnalysisAgent") in route_pairs
    assert ("ThemeAnalysisAgent", "ThemeConfigAssemblerAgent") in route_pairs

    lifecycle_tool = tools["lifecycle_tools"][0]
    assert lifecycle_tool["trigger"] == "before_chat"
    assert lifecycle_tool["function"] == "collect_prechat_theme_context"


def test_theme_capture_preload_collects_parent_theme_evidence() -> None:
    module = _load_module(
        "factory_app/workflows/ThemeCapture/tools/preload_theme_capture_context.py",
        "tests.preload_theme_capture_context",
    )

    context = {
        "parent_theme_config": {
            "theme": {"appearance": "dark", "branding": {"app_name": "MOZ-UI"}},
            "identity": {"app_name": "MOZ-UI"},
            "fonts": {
                "body": {"family": "Rajdhani"},
                "heading": {"family": "Orbitron"},
            },
            "colors": {
                "primary": {"main": "#7e5bef"},
                "secondary": {"main": "#ff49db"},
                "accent": {"main": "#67e8f9"},
            },
            "ui": {"chat": {"modes": {"ask": {}, "workflow": {}}}},
        }
    }

    result = asyncio.run(module.collect_prechat_theme_context(context_variables=context))

    assert result["success"] is True
    assert context["preloaded_context_ready"] is True
    assert context["preload_status"] == "ready"
    assert "parent_theme_config" in context["theme_capture_evidence"]["sources"]
    assert "#7e5bef" in context["theme_capture_evidence"]["colors"]
    assert "Rajdhani" in context["theme_capture_evidence"]["fonts"]
    assert context["app_name"] == "MOZ-UI"


def test_theme_capture_preload_reads_theme_and_shell_files(tmp_path) -> None:
    module = _load_module(
        "factory_app/workflows/ThemeCapture/tools/preload_theme_capture_context.py",
        "tests.preload_theme_capture_context_paths",
    )

    theme_path = tmp_path / "config" / "theme_config.json"
    shell_path = tmp_path / "config" / "shell.json"
    theme_path.parent.mkdir(parents=True, exist_ok=True)
    theme_path.write_text(
        """
        {
          "theme": { "appearance": "dark", "branding": { "app_name": "MOZ-UI" } },
          "identity": { "app_name": "MOZ-UI" },
          "fonts": {
            "body": { "family": "Rajdhani" },
            "heading": { "family": "Orbitron" }
          },
          "colors": {
            "primary": { "main": "#7e5bef" },
            "secondary": { "main": "#ff49db" },
            "accent": { "main": "#67e8f9" }
          },
          "ui": { "chat": { "modes": { "ask": {}, "workflow": {} } } }
        }
        """,
        encoding="utf-8",
    )
    shell_path.write_text(
        """
        {
          "header": { "logo": { "src": "logo.svg" } },
          "footer": { "links": [] }
        }
        """,
        encoding="utf-8",
    )

    context = {"parent_theme_config": str(theme_path), "parent_shell_config": str(shell_path)}
    result = asyncio.run(module.collect_prechat_theme_context(context_variables=context))

    assert result["success"] is True
    assert context["preload_status"] == "ready"
    assert "top-bar" in context["theme_capture_evidence"]["layout_hints"]
    assert "footer" in context["theme_capture_evidence"]["layout_hints"]


def test_theme_capture_preload_parses_css_snapshot_tokens() -> None:
    module = _load_module(
        "factory_app/workflows/ThemeCapture/tools/preload_theme_capture_context.py",
        "tests.preload_theme_capture_context_css",
    )

    context = {
        "css_snapshot": """
            @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600&family=Orbitron:wght@500;700&display=swap');
            :root {
              --color-primary: #7e5bef;
              --surface: rgba(255,255,255,0.08);
            }
            body {
              background: linear-gradient(180deg, #060B26 0%, #1A1F37 50%, #0F123B 100%);
              color: #ffffff;
              font-family: 'Rajdhani', sans-serif;
            }
            .sidebar {
              backdrop-filter: blur(12px);
            }
            h1 {
              font-family: 'Orbitron', sans-serif;
            }
        """,
    }

    result = asyncio.run(module.collect_prechat_theme_context(context_variables=context))

    assert result["success"] is True
    assert context["preload_status"] == "ready"
    assert "css_snapshot" in context["theme_capture_evidence"]["sources"]
    assert "#7e5bef" in context["theme_capture_evidence"]["colors"]
    assert "Rajdhani" in context["theme_capture_evidence"]["fonts"]
    assert "Orbitron" in context["theme_capture_evidence"]["fonts"]
    assert "glass" in context["theme_capture_evidence"]["layout_hints"]
    assert context["theme_capture_evidence"]["appearance"] == "dark"


def test_theme_capture_saver_persists_context_and_emits_preview(monkeypatch) -> None:
    module = _load_module(
        "factory_app/workflows/ThemeCapture/tools/save_captured_theme.py",
        "tests.save_captured_theme",
    )

    emitted = {}
    summary_artifact = {}

    async def _fake_emit(component_name, payload, **kwargs):
        emitted["component_name"] = component_name
        emitted["payload"] = payload
        emitted["kwargs"] = kwargs

    async def _fake_persist_summary_artifact(**kwargs):
        summary_artifact.update(kwargs)
        return type("ArtifactVersion", (), {"id": "av_theme_capture_1"})()

    monkeypatch.setattr(module, "emit_ui_surface", _fake_emit)
    monkeypatch.setattr(module, "_HAS_PERSISTENCE", False)
    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist_summary_artifact)

    context = {
        "structured_output": {
            "agent_message": "Theme captured.",
            "theme": {"appearance": "dark", "variant": "cosmic"},
            "identity": {"app_name": "MOZ-UI"},
            "fonts": {
                "body": {"family": "Rajdhani"},
                "heading": {"family": "Orbitron"},
            },
            "colors": {
                "primary": {"main": "#7e5bef"},
                "secondary": {"main": "#ff49db"},
                "accent": {"main": "#67e8f9"},
                "background": {"base": "#060B26", "surface": "#1A1F37"},
            },
            "shadows": {"primary": "0 0 10px rgba(255,255,255,1)"},
            "ui": {},
        },
        "chat_id": "chat-123",
        "app_id": "mozaiks-platform",
        "app_url": "https://example.test",
    }

    result = asyncio.run(module.save_captured_theme(context_variables=context))

    assert result["success"] is True
    assert context["theme_capture_persisted"] is True
    assert context["captured_theme_config"]["identity"]["app_name"] == "MOZ-UI"
    assert emitted["component_name"] == "ThemePreviewCard"
    assert emitted["kwargs"]["workflow_name"] == "ThemeCapture"
    assert summary_artifact["artifact_kind"] == "theme_capture"
    assert summary_artifact["summary_payload"]["theme_config"]["identity"]["app_name"] == "MOZ-UI"


def test_theme_capture_contract_targets_visual_tokens_not_shell_content() -> None:
    structured_outputs = _read_text("factory_app/workflows/ThemeCapture/structured_outputs.yaml")
    agents = _read_text("factory_app/workflows/ThemeCapture/agents.yaml")
    saver = _read_text("factory_app/workflows/ThemeCapture/tools/save_captured_theme.py")
    preview = _read_text("factory_app/workflows/ThemeCapture/ui/ThemeCapture/components/ThemePreviewCard.js")

    assert "ThemePrimitives" in structured_outputs
    assert "ShellUi" in structured_outputs
    assert "PageUi" in structured_outputs
    assert "chat_feed" in structured_outputs
    assert "`ui` must include `chat`, `shell`, and `page`." in agents
    assert "dark, light, or system" in agents
    assert "dark, light, or system." in structured_outputs
    assert "Treat `parent_shell_config` and discovered host chrome as layout evidence only" in agents
    assert "Do NOT output header actions, profile menus, notification text, or footer links." in agents
    assert "Do NOT emit shell content structures or anything that belongs in `config/shell.json`." in agents
    assert 'theme_v2.get("appearance", "system")' in saver
    assert 'appearance = "system"' in preview


def test_embed_surface_uses_canonical_runtime_flow() -> None:
    source = _read_text("chat-ui/src/embed/MozaiksEmbed.jsx")

    assert "apiAdapter.startChat(" in source
    assert "apiAdapter.createWebSocketConnection(" in source
    assert "apiAdapter.sendMessageToWorkflow(" in source
    assert "/ws/chat" not in source
    assert "user_message" not in source


def test_workflow_chat_uses_websocket_adapter_and_workflow_send_path() -> None:
    source = _read_text("chat-ui/src/components/chat/WorkflowChat.jsx")

    assert "new WebSocketApiAdapter" in source
    assert "workflowApi.startChat(" in source
    assert "workflowApi.sendMessageToWorkflow(" in source
    assert "wsSendMessage" not in source



