"""
Static contract tests for web_shell/vite.config.js.

These tests guard against regressions in the Vite resolver configuration.
They read the source file directly rather than executing Vite so they run
fast and without Node dependencies.
"""
from __future__ import annotations

import re
from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _vite_config() -> str:
    return (_workspace() / "web_shell" / "vite.config.js").read_text(encoding="utf-8")


def _styles_css() -> str:
    return (_workspace() / "web_shell" / "styles.css").read_text(encoding="utf-8")


# ── resolve.dedupe ──────────────────────────────────────────────────────────

class TestResolveDedupe:
    """
    web_shell/vite.config.js must declare resolve.dedupe for singleton packages.

    Background: chat-ui ships its own node_modules/ (including react, react-dom,
    and react-router). When the Vite dev server serves app workspace files or
    chat-ui source via the @fs/ virtual path, Vite may resolve those packages
    from the file's own node_modules directory instead of the shell's copy.
    Two distinct React instances in one browser session break hook and context
    state: useCallback, useContext, and router hooks all rely on a shared
    dispatcher that is bound to the React instance that called createRoot().

    resolve.dedupe instructs Vite to always use the shell's single copy of each
    listed package, regardless of which node_modules directory is encountered
    during resolution. This is necessary because:

      - chat-ui/node_modules/react exists alongside web_shell/node_modules/react
      - active app workspace code is served via @fs/ (outside the Vite root)
      - factory_app code is aliased and resolved from a separate directory tree

    Without this guard, any future npm install inside chat-ui that updates or
    re-creates its own react/react-router copy would silently reintroduce the
    duplicate-instance bug and break all hook-using components in the shell.
    """

    def test_resolve_dedupe_key_is_present(self) -> None:
        source = _vite_config()
        assert "dedupe:" in source, (
            "web_shell/vite.config.js must define resolve.dedupe to prevent "
            "duplicate React/router instances when app or chat-ui files are "
            "served via @fs/."
        )

    def test_dedupe_includes_react(self) -> None:
        source = _vite_config()
        # Match 'react' as a quoted string value inside the dedupe array.
        # Negative look-ahead avoids false-positives on 'react-dom' / 'react-router'.
        assert re.search(r"dedupe\s*:.*'react'", source, re.DOTALL) or \
               re.search(r"dedupe\s*:.*\"react\"", source, re.DOTALL), (
            "resolve.dedupe must include 'react'. A separate chat-ui react "
            "instance breaks hook state."
        )

    def test_dedupe_includes_react_dom(self) -> None:
        source = _vite_config()
        assert "'react-dom'" in source or '"react-dom"' in source, (
            "resolve.dedupe must include 'react-dom'."
        )
        # Verify it appears near dedupe, not just in the alias block.
        dedupe_block = _extract_dedupe_block(source)
        assert "react-dom" in dedupe_block, (
            "'react-dom' must appear in the dedupe array, not only in resolve.alias."
        )

    def test_dedupe_includes_react_router_dom(self) -> None:
        source = _vite_config()
        dedupe_block = _extract_dedupe_block(source)
        assert "react-router-dom" in dedupe_block, (
            "resolve.dedupe must include 'react-router-dom'. A duplicate router "
            "instance breaks useNavigate, useParams, and route context hooks."
        )

    def test_dedupe_includes_react_router(self) -> None:
        source = _vite_config()
        dedupe_block = _extract_dedupe_block(source)
        assert "react-router" in dedupe_block, (
            "resolve.dedupe must include 'react-router' (the base package). "
            "react-router-dom re-exports from react-router; both must be deduped."
        )

    def test_dedupe_comment_documents_rationale(self) -> None:
        source = _vite_config()
        # A comment near dedupe must mention why it is needed.
        # We check for key terms that explain the @fs/ / singleton concern.
        dedupe_region = _extract_dedupe_region(source)
        assert any(
            term in dedupe_region
            for term in ("singleton", "hooks", "@fs", "duplicate", "chat-ui")
        ), (
            "The resolve.dedupe declaration must have a comment documenting why "
            "it is required (singleton React, @fs/ serving, hook breakage, etc.)."
        )


# ── resolve.alias for React ─────────────────────────────────────────────────


class TestDependencyScannerJsx:
    """
    The Vite dev dependency scanner runs before the jsx-in-js transform plugin.

    web_shell imports first-party source files from chat-ui, factory_app, and
    active app workspaces. Some of those canonical UI files still use JSX inside
    .js modules. Vite 8 scans dependencies with Rolldown, so optimizeDeps must
    teach Rolldown to parse .js as JSX or local Studio startup fails before the
    app is served.
    """

    def test_rolldown_dependency_scan_parses_js_files_as_jsx(self) -> None:
        source = _vite_config()
        optimize_deps_region = _extract_object_region(source, "optimizeDeps:")
        assert "rolldownOptions" in optimize_deps_region, (
            "web_shell/vite.config.js must configure optimizeDeps.rolldownOptions "
            "for Vite 8 dependency scanning."
        )
        assert "moduleTypes" in optimize_deps_region, (
            "optimizeDeps.rolldownOptions.moduleTypes must be present so the "
            "dependency scanner can parse first-party JSX-in-.js files."
        )
        assert re.search(r"['\"]\.js['\"]\s*:\s*['\"]jsx['\"]", optimize_deps_region), (
            "optimizeDeps.rolldownOptions.moduleTypes must map '.js' to 'jsx' "
            "or Vite dev startup fails while scanning chat-ui/factory_app UI files."
        )


# ── Tailwind v4 source detection ─────────────────────────────────────────────

class TestTailwindV4PackagedSourceDetection:
    """
    The web shell is built from both repo-local source and pip-installed package
    source. Tailwind v4 automatic detection can traverse the wrong tree when
    web_shell is copied into a Docker build workspace, so styles.css must use
    explicit v4 CSS-first source detection and vite.config.js must expose the
    same chat/factory/app source roots that Vite aliases.
    """

    def test_stylesheet_uses_css_first_tailwind_v4_contract(self) -> None:
        styles = _styles_css()
        assert '@import "tailwindcss" source(none);' in styles
        assert "@config" not in styles, (
            "web_shell/styles.css must not load tailwind.config.js through "
            "@config. The legacy JS content contract can hang packaged "
            "production builds under Tailwind v4."
        )
        assert '@source "./.mozaiks-tailwind-sources";' in styles
        assert '@source not "./node_modules";' in styles
        assert "@theme" in styles
        assert "--color-primary: hsl(var(--mz-primary));" in styles
        assert "--color-muted-foreground: hsl(var(--mz-muted-foreground));" in styles
        assert "--radius-lg: var(--mz-radius);" in styles
        assert "--font-chat-body:" in styles
        assert "--border-width-3: 3px;" in styles

    def test_vite_config_populates_tailwind_source_links(self) -> None:
        source = _vite_config()
        assert "ensureTailwindSourceLinks" in source
        assert "tailwindSourceLinkRoot" in source
        assert "fs.symlinkSync" in source
        assert "'.mozaiks-tailwind-sources'" in source
        assert "['chat-ui-src', chatUiSrcRoot]" in source
        assert "['factory-app-ui', path.resolve(factoryAppRoot, 'app/ui')]" in source
        assert "['factory-workflows', factoryWorkflowsRoot]" in source
        assert "['platform-ui', path.resolve(platformAppDir, 'ui')]" in source
        assert "['platform-workflows', platformWorkflowRoot]" in source
        assert re.search(
            r"viteFsAllow\s*=\s*Array\.from\(new Set\(\[.*tailwindSourceLinkRoot",
            source,
            re.DOTALL,
        )


# ── resolve.alias for React ─────────────────────────────────────────────────

class TestReactAlias:
    """
    The resolve.alias block must pin React and react-dom to web_shell's copy.

    The alias is a belt-and-suspenders complement to dedupe: it ensures that
    even for packages not covered by the dedupe array, explicit imports of
    'react' resolve to a predictable single location.
    """

    def test_alias_pins_react_to_web_shell_node_modules(self) -> None:
        source = _vite_config()
        # Alias must point react to __dirname/node_modules/react.
        assert re.search(
            r"react\s*:\s*path\.resolve\s*\(\s*__dirname\s*,\s*['\"]node_modules/react['\"]",
            source,
        ), (
            "resolve.alias must pin 'react' to path.resolve(__dirname, 'node_modules/react') "
            "so all imports resolve to the web_shell's copy."
        )

    def test_alias_pins_react_dom_to_web_shell_node_modules(self) -> None:
        source = _vite_config()
        assert re.search(
            r"'react-dom'\s*:\s*path\.resolve\s*\(\s*__dirname\s*,\s*['\"]node_modules/react-dom['\"]",
            source,
        ), (
            "resolve.alias must pin 'react-dom' to web_shell's node_modules."
        )


# ── resolve.modules ordering ─────────────────────────────────────────────────

class TestResolveModules:
    """
    resolve.modules must list chatUiNodeModules before 'node_modules'.

    This ensures that packages shared across chat-ui and the shell (e.g.
    Radix UI, Tailwind plugins) are resolved from one canonical location.
    The dedupe list then prevents singleton packages from being loaded twice
    even when chat-ui's own node_modules is searched first.
    """

    def test_modules_includes_chatui_node_modules(self) -> None:
        source = _vite_config()
        assert "chatUiNodeModules" in source and "modules:" in source, (
            "resolve.modules must reference chatUiNodeModules so shared UI "
            "packages are resolved from chat-ui's dependency tree."
        )

    def test_modules_also_includes_web_shell_node_modules(self) -> None:
        source = _vite_config()
        # web_shell's node_modules must appear in the modules list as a fallback.
        assert re.search(
            r"modules\s*:\s*\[.*chatUiNodeModules.*path\.resolve\s*\(\s*__dirname",
            source,
            re.DOTALL,
        ), (
            "resolve.modules must include both chatUiNodeModules and "
            "path.resolve(__dirname, 'node_modules') so the resolver has a "
            "fallback for packages not present in chat-ui's tree."
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_dedupe_block(source: str) -> str:
    """
    Extract the content of the dedupe array from the source.
    Returns an empty string if not found.
    """
    match = re.search(r"dedupe\s*:\s*\[([^\]]*)\]", source, re.DOTALL)
    return match.group(1) if match else ""


def _extract_dedupe_region(source: str) -> str:
    """
    Return the 10 lines before and the dedupe array itself for comment inspection.
    """
    idx = source.find("dedupe:")
    if idx == -1:
        return ""
    start = max(0, idx - 400)
    end_match = re.search(r"dedupe\s*:\s*\[([^\]]*)\]", source[idx:], re.DOTALL)
    end = idx + (end_match.end() if end_match else 100)
    return source[start:end]


def _extract_object_region(source: str, marker: str) -> str:
    """
    Return the brace-delimited object region that follows a property marker.
    This is intentionally small and source-oriented; it only supports the
    static Vite config patterns these contract tests guard.
    """
    marker_idx = source.find(marker)
    if marker_idx == -1:
        return ""
    brace_idx = source.find("{", marker_idx)
    if brace_idx == -1:
        return ""

    depth = 0
    for idx in range(brace_idx, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_idx : idx + 1]
    return source[brace_idx:]

