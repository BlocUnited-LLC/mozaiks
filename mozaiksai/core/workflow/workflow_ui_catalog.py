from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class WorkflowUIPrimitive:
    primitive_id: str
    owner: str
    category: str
    default_display: str
    response_mode: str
    realization: str
    shipped_component: str | None
    plannable: bool
    description: str


_WORKFLOW_UI_PRIMITIVES: Tuple[WorkflowUIPrimitive, ...] = (
    WorkflowUIPrimitive(
        primitive_id="composer_reply",
        owner="shell",
        category="input",
        default_display="composer",
        response_mode="composer",
        realization="shell_builtin",
        shipped_component=None,
        plannable=True,
        description="Plain natural-language user reply through the standard chat composer.",
    ),
    WorkflowUIPrimitive(
        primitive_id="approval_card",
        owner="workflow",
        category="interaction",
        default_display="inline",
        response_mode="actions",
        realization="shipped_component",
        shipped_component="ApprovalCard",
        plannable=True,
        description="Binary or ternary approval checkpoint with optional rationale.",
    ),
    WorkflowUIPrimitive(
        primitive_id="choice_picker",
        owner="workflow",
        category="interaction",
        default_display="inline",
        response_mode="selection",
        realization="shipped_component",
        shipped_component="ChoicePicker",
        plannable=True,
        description="Single-select or multi-select choice UI for bounded options.",
    ),
    WorkflowUIPrimitive(
        primitive_id="confirmation_summary",
        owner="workflow",
        category="interaction",
        default_display="inline",
        response_mode="actions",
        realization="shipped_component",
        shipped_component="ConfirmationSummary",
        plannable=True,
        description="Structured summary of captured facts with confirm or revise actions.",
    ),
    WorkflowUIPrimitive(
        primitive_id="form_card",
        owner="workflow",
        category="interaction",
        default_display="inline",
        response_mode="form",
        realization="shipped_component",
        shipped_component="FormCard",
        plannable=True,
        description="Typed structured form for multi-field user input and validation.",
    ),
    WorkflowUIPrimitive(
        primitive_id="record_picker",
        owner="workflow",
        category="interaction",
        default_display="artifact",
        response_mode="selection",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Search, browse, and select one or more records from a dataset.",
    ),
    WorkflowUIPrimitive(
        primitive_id="file_upload_prompt",
        owner="workflow",
        category="interaction",
        default_display="inline",
        response_mode="upload",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Prompt for one or more files with validation and upload progress.",
    ),
    WorkflowUIPrimitive(
        primitive_id="diff_review",
        owner="workflow",
        category="review",
        default_display="artifact",
        response_mode="actions",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Before-and-after comparison for edits, patches, or revisions.",
    ),
    WorkflowUIPrimitive(
        primitive_id="action_plan_review",
        owner="workflow",
        category="review",
        default_display="artifact",
        response_mode="actions",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Review and optionally approve a generated action plan or checklist.",
    ),
    WorkflowUIPrimitive(
        primitive_id="artifact_workbench",
        owner="workflow",
        category="artifact",
        default_display="artifact",
        response_mode="actions",
        realization="shipped_component",
        shipped_component="ArtifactWorkbench",
        plannable=True,
        description="Multi-pane artifact workspace for code, preview, exports, and next-step actions.",
    ),
    WorkflowUIPrimitive(
        primitive_id="document_preview",
        owner="workflow",
        category="artifact",
        default_display="artifact",
        response_mode="none",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Read-only preview of generated documents, reports, or summaries.",
    ),
    WorkflowUIPrimitive(
        primitive_id="download_center",
        owner="workflow",
        category="artifact",
        default_display="artifact",
        response_mode="actions",
        realization="shipped_component",
        shipped_component="DownloadCenter",
        plannable=True,
        description="Artifact bundle with download links, export actions, and file metadata.",
    ),
    WorkflowUIPrimitive(
        primitive_id="comparison_board",
        owner="workflow",
        category="review",
        default_display="artifact",
        response_mode="selection",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Side-by-side comparison surface for options, variants, or strategies.",
    ),
    WorkflowUIPrimitive(
        primitive_id="diagram_viewer",
        owner="workflow",
        category="artifact",
        default_display="artifact",
        response_mode="none",
        realization="shipped_component",
        shipped_component="DiagramViewer",
        plannable=True,
        description="Diagram or sequence visualization for read-only or lightly interactive review.",
    ),
    WorkflowUIPrimitive(
        primitive_id="table_review",
        owner="workflow",
        category="review",
        default_display="artifact",
        response_mode="selection",
        realization="generated_component",
        shipped_component=None,
        plannable=True,
        description="Row-based review table with select, approve, or bulk actions.",
    ),
    WorkflowUIPrimitive(
        primitive_id="run_status_banner",
        owner="shell",
        category="status",
        default_display="inline",
        response_mode="none",
        realization="shell_builtin",
        shipped_component=None,
        plannable=False,
        description="Framework-owned workflow run state banner (running, paused, failed, complete).",
    ),
    WorkflowUIPrimitive(
        primitive_id="progress_stepper",
        owner="shell",
        category="status",
        default_display="inline",
        response_mode="none",
        realization="shell_builtin",
        shipped_component=None,
        plannable=False,
        description="Framework-owned stage progress indicator driven by workflow planning and runtime status.",
    ),
    WorkflowUIPrimitive(
        primitive_id="agent_activity_feed",
        owner="shell",
        category="status",
        default_display="inline",
        response_mode="none",
        realization="shell_builtin",
        shipped_component=None,
        plannable=False,
        description="Framework-owned agent activity log for background execution and handoffs.",
    ),
    WorkflowUIPrimitive(
        primitive_id="awaiting_reply_banner",
        owner="shell",
        category="status",
        default_display="composer",
        response_mode="none",
        realization="shell_builtin",
        shipped_component=None,
        plannable=False,
        description="Framework-owned pending-input banner that routes the next composer reply to the active workflow checkpoint.",
    ),
)

_WORKFLOW_UI_REALIZATION_IDS: Tuple[str, ...] = (
    "shell_builtin",
    "shipped_component",
    "workflow_wrapper",
    "generated_component",
)


def get_workflow_ui_primitives() -> Tuple[WorkflowUIPrimitive, ...]:
    return _WORKFLOW_UI_PRIMITIVES


def get_workflow_ui_realization_ids(*, include_shell_builtin: bool = True) -> Tuple[str, ...]:
    if include_shell_builtin:
        return _WORKFLOW_UI_REALIZATION_IDS
    return tuple(value for value in _WORKFLOW_UI_REALIZATION_IDS if value != "shell_builtin")


def get_workflow_ui_primitive_ids(*, include_shell_status: bool = True) -> Tuple[str, ...]:
    primitives = _WORKFLOW_UI_PRIMITIVES
    if not include_shell_status:
        primitives = tuple(entry for entry in primitives if entry.plannable)
    return tuple(entry.primitive_id for entry in primitives)


def get_workflow_renderable_primitives() -> Tuple[WorkflowUIPrimitive, ...]:
    return tuple(
        entry
        for entry in _WORKFLOW_UI_PRIMITIVES
        if entry.plannable and entry.realization in {"generated_component", "shipped_component"}
    )


def get_workflow_renderable_primitive_ids() -> Tuple[str, ...]:
    return tuple(entry.primitive_id for entry in get_workflow_renderable_primitives())


def get_workflow_shipped_component_primitives() -> Tuple[WorkflowUIPrimitive, ...]:
    return tuple(
        entry
        for entry in _WORKFLOW_UI_PRIMITIVES
        if entry.plannable and entry.realization == "shipped_component" and entry.shipped_component
    )


def get_workflow_shipped_component_names() -> Tuple[str, ...]:
    return tuple(entry.shipped_component for entry in get_workflow_shipped_component_primitives() if entry.shipped_component)


def get_workflow_shipped_component_map() -> Tuple[Tuple[str, str], ...]:
    return tuple(
        (entry.primitive_id, entry.shipped_component)
        for entry in get_workflow_shipped_component_primitives()
        if entry.shipped_component
    )


def infer_workflow_ui_realization(
    primitive_id: Optional[str],
    component_name: Optional[str],
) -> Optional[str]:
    primitive_text = str(primitive_id or "").strip()
    if not primitive_text:
        return None

    primitive = next((entry for entry in _WORKFLOW_UI_PRIMITIVES if entry.primitive_id == primitive_text), None)
    if primitive is None:
        return None

    component_text = str(component_name or "").strip()
    if primitive.realization == "shell_builtin":
        return "shell_builtin"
    if primitive.realization == "shipped_component":
        if primitive.shipped_component and component_text and component_text != primitive.shipped_component:
            return "workflow_wrapper"
        return "shipped_component"
    return "generated_component"


def validate_workflow_ui_realization_ids(
    values: Iterable[str] | None,
    *,
    context: str,
    include_shell_builtin: bool = True,
) -> List[str]:
    allowed_names = get_workflow_ui_realization_ids(include_shell_builtin=include_shell_builtin)
    allowed = set(allowed_names)
    normalized: List[str] = []
    invalid: List[str] = []

    if values is None:
        return normalized

    for value in values:
        if not isinstance(value, str):
            invalid.append(repr(value))
            continue
        realization_id = value.strip()
        if not realization_id:
            continue
        if realization_id not in allowed:
            invalid.append(realization_id)
            continue
        if realization_id not in normalized:
            normalized.append(realization_id)

    if invalid:
        allowed_text = ", ".join(allowed_names)
        invalid_text = ", ".join(invalid)
        raise ValueError(
            f"{context} references unsupported workflow UI realizations: {invalid_text}. "
            f"Allowed realizations: {allowed_text}"
        )

    return normalized


def validate_workflow_ui_primitive_ids(
    values: Iterable[str] | None,
    *,
    context: str,
    include_shell_status: bool = False,
) -> List[str]:
    allowed_names = get_workflow_ui_primitive_ids(include_shell_status=include_shell_status)
    allowed = set(allowed_names)
    normalized: List[str] = []
    invalid: List[str] = []

    if values is None:
        return normalized

    for value in values:
        if not isinstance(value, str):
            invalid.append(repr(value))
            continue
        primitive_id = value.strip()
        if not primitive_id:
            continue
        if primitive_id not in allowed:
            invalid.append(primitive_id)
            continue
        if primitive_id not in normalized:
            normalized.append(primitive_id)

    if invalid:
        allowed_text = ", ".join(allowed_names)
        invalid_text = ", ".join(invalid)
        raise ValueError(
            f"{context} references unsupported workflow UI primitives: {invalid_text}. "
            f"Allowed primitives: {allowed_text}"
        )

    return normalized


def validate_workflow_renderable_primitive_ids(
    values: Iterable[str] | None,
    *,
    context: str,
) -> List[str]:
    allowed_names = get_workflow_renderable_primitive_ids()
    allowed = set(allowed_names)
    normalized: List[str] = []
    invalid: List[str] = []

    if values is None:
        return normalized

    for value in values:
        if not isinstance(value, str):
            invalid.append(repr(value))
            continue
        primitive_id = value.strip()
        if not primitive_id:
            continue
        if primitive_id not in allowed:
            invalid.append(primitive_id)
            continue
        if primitive_id not in normalized:
            normalized.append(primitive_id)

    if invalid:
        allowed_text = ", ".join(allowed_names)
        invalid_text = ", ".join(invalid)
        raise ValueError(
            f"{context} references unsupported renderable workflow UI primitives: {invalid_text}. "
            f"Allowed renderable primitives: {allowed_text}"
        )

    return normalized


def format_workflow_ui_catalog_guidance() -> str:
    lines: List[str] = ["Canonical workflow UI primitive catalog:"]

    lines.append("")
    lines.append("Plannable interaction/review primitives:")
    for entry in _WORKFLOW_UI_PRIMITIVES:
        if not entry.plannable:
            continue
        shipped_component = (
            f", shipped_component={entry.shipped_component}"
            if entry.shipped_component
            else ""
        )
        lines.append(
            f"- `{entry.primitive_id}` — owner={entry.owner}, display={entry.default_display}, "
            f"response={entry.response_mode}, realization={entry.realization}{shipped_component}. {entry.description}"
        )

    lines.append("")
    lines.append("Framework-owned shell status primitives (do not emit as workflow-local UI components):")
    for entry in _WORKFLOW_UI_PRIMITIVES:
        if entry.plannable:
            continue
        lines.append(
            f"- `{entry.primitive_id}` — owner={entry.owner}, display={entry.default_display}, "
            f"realization={entry.realization}. {entry.description}"
        )

    lines.append("")
    lines.append("Rules:")
    lines.append("- Use `composer_reply` for plain natural-language feedback instead of generating an inline text box.")
    lines.append("- Use a plannable workflow primitive when the user must approve, choose, edit, upload, or review structured content.")
    lines.append("- Do not encode shell status surfaces as workflow-local React components; the shell owns progress, status, and background activity UX.")
    lines.append("- For `shipped_component` primitives, prefer the canonical shipped component name directly in `ui.component`.")
    lines.append("- If a `shipped_component` primitive needs workflow-local branding or extra glue, generate only a thin re-export or wrapper around the shipped component.")
    lines.append("- Generated custom React should only be produced for entries whose realization is `generated_component`.")

    return "\n".join(lines)


__all__ = [
    "WorkflowUIPrimitive",
    "format_workflow_ui_catalog_guidance",
    "get_workflow_renderable_primitive_ids",
    "get_workflow_renderable_primitives",
    "get_workflow_shipped_component_map",
    "get_workflow_shipped_component_names",
    "get_workflow_shipped_component_primitives",
    "get_workflow_ui_realization_ids",
    "get_workflow_ui_primitives",
    "get_workflow_ui_primitive_ids",
    "infer_workflow_ui_realization",
    "validate_workflow_ui_realization_ids",
    "validate_workflow_renderable_primitive_ids",
    "validate_workflow_ui_primitive_ids",
]
