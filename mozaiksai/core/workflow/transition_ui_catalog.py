from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransitionUIPrimitive:
    primitive_id: str
    owner: str
    kind: str
    import_path: str
    use_when: str
    notes: str


_TRANSITION_UI_PRIMITIVES: tuple[TransitionUIPrimitive, ...] = (
    TransitionUIPrimitive(
        primitive_id="LauncherScreen",
        owner="shell",
        kind="renderer",
        import_path="shell built-in",
        use_when="A lightweight choice screen can be expressed with ui.props only.",
        notes="Generic shell-owned choice renderer for transition routes.",
    ),
    TransitionUIPrimitive(
        primitive_id="ConfirmScreen",
        owner="shell",
        kind="renderer",
        import_path="shell built-in",
        use_when="The transition is a simple continue/cancel or confirm/dismiss gate.",
        notes="Shell-owned confirm renderer for simple approval checkpoints.",
    ),
    TransitionUIPrimitive(
        primitive_id="TransitionChoicePanel",
        owner="shell-shared",
        kind="component",
        import_path="@mozaiks/chat-ui/platform",
        use_when="A branded transition needs custom copy and a cohesive modal choice layout.",
        notes="Owns the modal body structure inside the shell overlay.",
    ),
    TransitionUIPrimitive(
        primitive_id="TransitionChoiceCard",
        owner="shell-shared",
        kind="component",
        import_path="@mozaiks/chat-ui/platform",
        use_when="A branded transition needs reusable full-card options with image, badge, helper text, CTA, or disabled state.",
        notes="Owns the reusable full-card option treatment.",
    ),
    TransitionUIPrimitive(
        primitive_id="useTransitionChoiceMotion",
        owner="shell-shared",
        kind="hook",
        import_path="@mozaiks/chat-ui/platform",
        use_when="A custom transition wrapper needs shared entry motion while respecting prefers-reduced-motion.",
        notes="Owns shared transition motion behavior.",
    ),
)


def get_transition_ui_primitives() -> tuple[TransitionUIPrimitive, ...]:
    return _TRANSITION_UI_PRIMITIVES


def format_transition_ui_catalog_guidance() -> str:
    lines: list[str] = ["Canonical transition UI primitive catalog:"]

    lines.append("")
    for entry in _TRANSITION_UI_PRIMITIVES:
        lines.append(
            f"- `{entry.primitive_id}` — owner={entry.owner}, kind={entry.kind}, "
            f"import={entry.import_path}. Use when: {entry.use_when} {entry.notes}"
        )

    lines.append("")
    lines.append("Rules:")
    lines.append("- Transition routing stays declarative in `extended_orchestration/extension_registry.json`; primitives do not own `route_to` or transition-seeded context variables.")
    lines.append("- Prefer `LauncherScreen` or `ConfirmScreen` when shell props are sufficient; do not create a workflow-local wrapper just to restyle a simple transition.")
    lines.append("- For branded transitions, workflow-local React wrappers may compose `TransitionChoicePanel`, `TransitionChoiceCard`, and `useTransitionChoiceMotion` from `@mozaiks/chat-ui/platform`.")
    lines.append("- Do not import `TransitionOverlayFrame` inside workflow-local transition files; the shell owns backdrop, dialog semantics, focus trap, Escape handling, and scroll lock.")
    lines.append("- Do not create reusable transition primitive helpers under workflow folders; keep workflow-local files focused on product-specific copy, imagery, and semantic option mapping.")
    lines.append("- Transition wrappers are React-only files; they are not `UI_Tool` pairs and should not generate a Python `use_ui_tool(...)` half.")

    return "\n".join(lines)


__all__ = [
    "TransitionUIPrimitive",
    "format_transition_ui_catalog_guidance",
    "get_transition_ui_primitives",
]