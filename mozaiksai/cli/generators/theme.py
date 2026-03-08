"""Generate Keycloak login theme CSS from brand.json.

Reads ``app/brand/public/brand.json``, resolves the target Keycloak theme
name from ``app/app.json`` (``auth.keycloak.themeName``), renders
``infra/keycloak/themes/<themeName>/login/resources/css/login.css.tmpl``,
and copies branded assets (logo, background) into the theme resources.

The CSS template uses ``{{path.to.value}}`` tokens resolved against the
brand.json dict, for example: ``{{colors.primary.main}}`` -> ``#06b6d4``.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from mozaiksai.cli.paths import find_project_root, load_json


# ── Standard theme.properties ────────────────────────────────────────────────

THEME_PROPERTIES = """\
parent=keycloak
import=common/keycloak

styles=css/login.css
"""

# Asset mappings: brand.json assets key → theme filename
ASSET_MAP = {
    "logo": "logo.svg",
    "backgroundImage": "bg.png",
}


# ── Token resolution ─────────────────────────────────────────────────────────

TOKEN_RE = re.compile(r"\{\{(.+?)\}\}")


def resolve_token(brand: dict, path: str) -> str:
    """
    Resolve a dotted path like ``colors.primary.main`` from the brand dict.

    Raises KeyError with a helpful message if the path doesn't exist.
    """
    parts = path.split(".")
    node = brand
    for i, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            traversed = ".".join(parts[: i + 1])
            raise KeyError(
                f"Token '{path}' failed at '{traversed}' — "
                "key not found in brand.json"
            )
        node = node[part]
    return str(node)


def render_template(template: str, brand: dict) -> str:
    """
    Replace all ``{{path.to.value}}`` tokens in template with brand.json values.

    Returns the rendered CSS string.
    """
    missing: list[str] = []

    def replacer(match: re.Match) -> str:
        path = match.group(1).strip()
        try:
            return resolve_token(brand, path)
        except KeyError as exc:
            missing.append(str(exc))
            return f"/* MISSING: {path} */"

    result = TOKEN_RE.sub(replacer, template)

    if missing:
        print("WARNING: Some brand tokens could not be resolved:", file=sys.stderr)
        for msg in missing:
            print(f"  - {msg}", file=sys.stderr)

    return result


# ── Asset copying ────────────────────────────────────────────────────────────

def copy_assets(
    brand: dict,
    assets_dir: Path,
    img_output: Path,
    *,
    dry_run: bool = False,
) -> list[str]:
    """
    Copy brand assets (logo, background) into the Keycloak theme resources.

    Returns a list of human-readable descriptions of copied files.
    """
    copied: list[str] = []
    assets = brand.get("assets", {})

    for brand_key, theme_filename in ASSET_MAP.items():
        source_name = assets.get(brand_key)
        if not source_name:
            continue

        source = assets_dir / source_name
        dest = img_output / theme_filename

        if not source.is_file():
            print(
                f"  WARNING: Asset '{source_name}' declared in brand.json "
                f"but not found at {source}",
                file=sys.stderr,
            )
            continue

        if dry_run:
            copied.append(f"(dry) {source.name} -> {theme_filename}")
        else:
            img_output.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            copied.append(
                f"{source.name} -> {theme_filename} "
                f"({dest.stat().st_size:,} bytes)"
            )

    return copied


# ── Public API ───────────────────────────────────────────────────────────────

def run(
    *,
    root: Path | None = None,
    dry_run: bool = False,
    check: bool = False,
) -> int:
    """
    Generate the Keycloak login theme from brand.json.

    Returns 0 on success, 1 on failure.
    """
    root = root or find_project_root()

    app_json_path = root / "app" / "app.json"
    brand_json_path = root / "app" / "brand" / "public" / "brand.json"
    assets_dir = root / "app" / "brand" / "public" / "assets"
    app = load_json(app_json_path, "app.json")
    theme_name = (
        app.get("auth", {})
        .get("keycloak", {})
        .get("themeName", "mozaiks")
    )
    theme_dir = root / "infra" / "keycloak" / "themes" / theme_name / "login"
    base_theme_dir = root / "infra" / "keycloak" / "themes" / "mozaiks" / "login"
    css_template = theme_dir / "resources" / "css" / "login.css.tmpl"
    css_output = theme_dir / "resources" / "css" / "login.css"
    img_output = theme_dir / "resources" / "img"
    theme_props = theme_dir / "theme.properties"

    brand = load_json(brand_json_path, "brand.json")

    # ── Render CSS template ──────────────────────────────────────────────
    if not css_template.is_file():
        fallback_template = base_theme_dir / "resources" / "css" / "login.css.tmpl"
        if theme_name != "mozaiks" and fallback_template.is_file():
            print(
                f"INFO: Theme template not found at {css_template}. "
                f"Using fallback template {fallback_template}.",
                file=sys.stderr,
            )
            css_template = fallback_template
        else:
            print(f"ERROR: CSS template not found at {css_template}", file=sys.stderr)
            return 1

    template = css_template.read_text(encoding="utf-8")
    css = render_template(template, brand)

    # ── Check mode ───────────────────────────────────────────────────────
    if check:
        if not css_output.is_file():
            print(
                "FAIL: login.css does not exist — "
                "run 'python -m mozaiksai.cli generate --theme' to generate it"
            )
            return 1
        current = css_output.read_text(encoding="utf-8")
        if current == css:
            print("OK: Keycloak theme is up-to-date with brand.json")
            return 0
        else:
            print(
                "FAIL: login.css is out of date — "
                "run 'python -m mozaiksai.cli generate --theme' to regenerate"
            )
            return 1

    # ── Dry run ──────────────────────────────────────────────────────────
    if dry_run:
        print(css)
        print(f"\n(dry run — would write to {css_output})", file=sys.stderr)
        return 0

    # ── Write CSS ────────────────────────────────────────────────────────
    css_output.parent.mkdir(parents=True, exist_ok=True)
    css_output.write_text(css, encoding="utf-8")

    # ── Copy assets ──────────────────────────────────────────────────────
    asset_log = copy_assets(brand, assets_dir, img_output)

    # ── Write theme.properties ───────────────────────────────────────────
    theme_props.parent.mkdir(parents=True, exist_ok=True)
    theme_props.write_text(THEME_PROPERTIES, encoding="utf-8")

    # ── Summary ──────────────────────────────────────────────────────────
    brand_name = brand.get("name", "Unknown")
    primary = brand.get("colors", {}).get("primary", {}).get("main", "?")
    font_body = brand.get("fonts", {}).get("body", {}).get("family", "?")
    font_heading = brand.get("fonts", {}).get("heading", {}).get("family", "?")

    print("OK: Generated Keycloak theme from brand.json")
    print(f"  Theme: {theme_name}")
    print(f"  Brand: {brand_name}")
    print(f"  Primary: {primary}")
    print(f"  Fonts: {font_heading} (heading) / {font_body} (body)")
    print(f"  CSS: {css_output.relative_to(root)} ({css_output.stat().st_size:,} bytes)")
    if asset_log:
        print("  Assets:")
        for line in asset_log:
            print(f"    {line}")
    print(f"  Properties: {theme_props.relative_to(root)}")
    return 0
