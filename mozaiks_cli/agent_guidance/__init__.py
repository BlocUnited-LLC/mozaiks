"""App-workspace coding-agent guidance templates packaged with the Mozaiks CLI."""

from __future__ import annotations

from pathlib import Path

AGENT_GUIDANCE_BLOCK_NAME = "agent-guidance"
AGENT_GUIDANCE_BEGIN = f"<!-- BEGIN MOZAIKS MANAGED: {AGENT_GUIDANCE_BLOCK_NAME} -->"
AGENT_GUIDANCE_END = f"<!-- END MOZAIKS MANAGED: {AGENT_GUIDANCE_BLOCK_NAME} -->"

GUIDANCE_FILE_SPECS: tuple[tuple[Path, str, bool], ...] = (
    (Path("AGENTS.md"), "templates/AGENTS.md", True),
    (Path("CLAUDE.md"), "templates/CLAUDE.md", True),
    (Path(".claude/rules/app-bundle.md"), "rules/app-bundle.md", False),
    (Path(".claude/rules/docs.md"), "rules/docs.md", False),
    (Path(".claude/rules/frontend.md"), "rules/frontend.md", False),
    (Path(".claude/rules/modules.md"), "rules/modules.md", False),
    (Path(".claude/rules/multi-agent-coordination.md"), "rules/multi-agent-coordination.md", False),
    (Path(".claude/rules/workflows.md"), "rules/workflows.md", False),
    (Path(".claude/skills/add-branding/SKILL.md"), "skills/add-branding/SKILL.md", False),
    (Path(".claude/skills/add-module/SKILL.md"), "skills/add-module/SKILL.md", False),
    (Path(".claude/skills/add-page/SKILL.md"), "skills/add-page/SKILL.md", False),
    (Path(".claude/skills/create-workflow/SKILL.md"), "skills/create-workflow/SKILL.md", False),
    (Path(".claude/skills/docs-maintenance/SKILL.md"), "skills/docs-maintenance/SKILL.md", False),
    (Path(".claude/skills/setup/SKILL.md"), "skills/setup/SKILL.md", False),
)


def resolve_agent_guidance_root() -> Path:
    """Return the root of the packaged agent guidance tree."""
    return Path(__file__).parent


def build_agent_guidance_files(app_name: str, preset: str) -> dict[Path, str]:
    """Return package-maintained coding-agent guidance for an app workspace."""
    guidance_root = resolve_agent_guidance_root()
    substitutions = {"app_name": app_name, "preset": preset}
    files: dict[Path, str] = {}

    for relative_path, source_path, should_render in GUIDANCE_FILE_SPECS:
        content = (guidance_root / source_path).read_text(encoding="utf-8")
        if should_render:
            content = content.format_map(substitutions)
        files[relative_path] = _with_agent_guidance_managed_block(content)

    return files


def _with_agent_guidance_managed_block(content: str) -> str:
    """Wrap generated content in a managed block while preserving skill frontmatter."""
    normalized = content.strip()
    prefix = ""
    body = normalized
    if normalized.startswith("---\n"):
        closing_index = normalized.find("\n---\n", 4)
        if closing_index != -1:
            prefix = normalized[: closing_index + len("\n---\n")].rstrip() + "\n\n"
            body = normalized[closing_index + len("\n---\n") :].strip()
    return f"{prefix}{AGENT_GUIDANCE_BEGIN}\n{body}\n{AGENT_GUIDANCE_END}\n"


__all__ = [
    "AGENT_GUIDANCE_BEGIN",
    "AGENT_GUIDANCE_END",
    "GUIDANCE_FILE_SPECS",
    "build_agent_guidance_files",
    "resolve_agent_guidance_root",
]
