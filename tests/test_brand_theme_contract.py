from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "factory_app" / "app"

VISUAL_TOKEN_KEYS = {
    "appearance",
    "background",
    "border",
    "color",
    "colors",
    "density",
    "font",
    "fontFamily",
    "font-family",
    "fonts",
    "foreground",
    "primitives",
    "radius",
    "shadow",
    "shadows",
    "theme",
    "ui",
}

HEX_OR_COLOR_FUNCTION_RE = re.compile(
    r"#[0-9A-Fa-f]{3,8}\b|(?<![\w-])(?:rgb|rgba|hsl|hsla)\(",
)
FONT_DECLARATION_RE = re.compile(
    r"fontFamily|font-family|@font-face|\b(?:Rajdhani|Orbitron|Fagrak|Inter|Montserrat|Poppins)\b",
)
LOCAL_PRIMITIVE_CLONE_RE = re.compile(
    r"\b(?:function|const|class)\s+(?:StatusPill|MetricTile|StatCard)\b",
)


def _load_json(relative: str) -> dict:
    path = APP_ROOT / relative
    assert path.is_file(), f"Missing app contract file: {relative}"
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
    else:
        yield value


def _iter_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def _app_visual_surface_files() -> list[Path]:
    roots = [
        APP_ROOT / "ui" / "pages" / "custom",
        APP_ROOT / "admin" / "pages",
        APP_ROOT / "ui" / "components",
    ]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(
                path
                for path in root.rglob("*")
                if path.suffix in {".js", ".jsx", ".ts", ".tsx"}
            )
    return files


def test_brand_theme_config_is_visual_authority() -> None:
    theme = _load_json("brand/theme_config.json")

    assert "theme" in theme
    assert "fonts" in theme
    assert "colors" in theme
    assert "shadows" in theme
    assert "primitives" in theme
    assert "ui" in theme

    compact_theme = theme["theme"]
    for key in ("primary", "radius", "font", "font_heading", "appearance", "density"):
        assert key in compact_theme


def test_shell_config_does_not_own_visual_tokens() -> None:
    shell = _load_json("config/shell.json")

    leaked_keys = sorted(set(_iter_keys(shell)) & VISUAL_TOKEN_KEYS)
    assert leaked_keys == []

    leaked_values = [
        value
        for value in _iter_values(shell)
        if isinstance(value, str) and HEX_OR_COLOR_FUNCTION_RE.search(value)
    ]
    assert leaked_values == []


def test_app_visual_surfaces_do_not_hardcode_brand_values() -> None:
    violations: list[str] = []
    for path in _app_visual_surface_files():
        source = path.read_text(encoding="utf-8")
        for pattern, label in (
            (HEX_OR_COLOR_FUNCTION_RE, "hardcoded color"),
            (FONT_DECLARATION_RE, "literal font"),
            (LOCAL_PRIMITIVE_CLONE_RE, "local visual primitive clone"),
        ):
            for match in pattern.finditer(source):
                line_number = source.count("\n", 0, match.start()) + 1
                relative = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{relative}:{line_number}: {label}: {match.group(0)}")

    assert violations == []


def test_local_fonts_remain_under_brand_fonts_and_are_referenced_by_url() -> None:
    theme = _load_json("brand/theme_config.json")

    font_files = [
        path
        for path in APP_ROOT.rglob("*")
        if path.suffix.lower() in {".otf", ".ttf", ".woff", ".woff2"}
    ]
    assert font_files, "Expected local brand fonts to live under app/brand/fonts"
    for path in font_files:
        assert APP_ROOT / "brand" / "fonts" in path.parents

    for font in theme.get("fonts", {}).values():
        if isinstance(font, dict) and font.get("localFont"):
            src = font.get("src")
            assert isinstance(src, str)
            assert src.startswith("/fonts/")
