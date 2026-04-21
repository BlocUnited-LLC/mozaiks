from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_page_frame_exists_and_owns_outer_layout() -> None:
    source = _read("chat-ui/src/ui/page-renderer/PageFrame.jsx")
    assert "FRAME_WIDTH_VARS" in source
    assert "bg-transparent" in source
    assert "mx-auto flex w-full flex-col" in source


def test_page_renderer_uses_page_frame() -> None:
    source = _read("chat-ui/src/ui/page-renderer/PageRenderer.jsx")
    assert "import { PageFrame } from './PageFrame.jsx';" in source
    assert "<PageFrame" in source


def test_page_renderer_uses_schema_adapter_layers() -> None:
    renderer_source = _read("chat-ui/src/ui/page-renderer/PageRenderer.jsx")
    section_source = _read("chat-ui/src/ui/page-renderer/SectionRenderer.jsx")
    data_source = _read("chat-ui/src/ui/page-renderer/usePageData.js")

    assert "normalizeSections(rawSections)" in renderer_source
    assert "emitAppEvent" in section_source
    assert "getChildSections" in section_source
    assert "flattenSections(sections)" in data_source


def test_schema_page_no_longer_owns_page_padding() -> None:
    source = _read("chat-ui/src/ui/screens/SchemaPage.jsx")
    assert "min-h-screen bg-background p-6 md:p-8" not in source
    assert "onNavigate={navigate}" in source


def test_platform_dashboard_uses_shared_page_frame() -> None:
    source = _read("mozaiks-platform/ui/pages/Dashboard.jsx")
    assert "import { PageFrame } from '@mozaiks/chat-ui';" in source
    assert "<PageFrame" in source
