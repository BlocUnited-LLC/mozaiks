from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field

from .assembly_phase import assemble_features
from .code_file_utils import collect_generated_app_file_entries


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "passed", "ready"}
    return bool(value)


async def assemble_app_tasks(
    *,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    app_id = None
    feature_outputs: List[Dict[str, Any]] = []
    inject_key: Optional[str] = None

    if context_variables and hasattr(context_variables, "get"):
        quality_status = context_variables.get("app_ui_quality_status")
        if quality_status != "passed":
            warnings = context_variables.get("app_ui_quality_warnings") or []
            warning_text = ""
            if isinstance(warnings, list) and warnings:
                warning_text = " Warnings: " + "; ".join(str(item) for item in warnings)
            raise ValueError(
                "app_ui_quality_status must be 'passed' before assembly. "
                f"Current status: {quality_status or 'missing'}.{warning_text}"
            )

        if _is_truthy(context_variables.get("app_schema_ready")):
            generated_app_dir = context_variables.get("generated_app_dir")
            code_files = collect_generated_app_file_entries(generated_app_dir)
            if not code_files:
                raise ValueError(
                    "app_schema_ready is true, but generated_app_dir does not contain "
                    "collectable app artifacts."
                )
            try:
                context_variables.set(
                    "generated_files",
                    {
                        str(item["filename"]): str(item["content"])
                        for item in code_files
                    },
                )
                context_variables.set("assembled_source", "schema_artifacts")
            except Exception:
                pass
            return {
                "code_files": code_files,
                "agent_message": (
                    f"Assembled {len(code_files)} files from persisted app schema artifacts."
                ),
            }

        app_id = context_variables.get("app_id")
        raw_inject_key = context_variables.get("_mfj_resume_inject_as")
        if isinstance(raw_inject_key, str) and raw_inject_key.strip():
            inject_key = raw_inject_key.strip()
        else:
            inject_key = "mfj_app_task_results"

        merged = context_variables.get(inject_key)
        if isinstance(merged, dict):
            for key, value in merged.items():
                if key == "_failed":
                    continue
                if isinstance(value, dict):
                    feature_outputs.append(value)

    if not app_id:
        raise ValueError("app_id is required to assemble task outputs")

    result = await assemble_features(
        app_id=str(app_id),
        feature_outputs=feature_outputs,
    )

    status_note = result.get("message") or "Assembled app task outputs into one bundle."
    if isinstance(inject_key, str) and inject_key:
        status_note = f"{status_note} (source={inject_key})"

    return {
        "code_files": result.get("code_files", []),
        "agent_message": status_note,
    }


__all__ = ["assemble_app_tasks"]
