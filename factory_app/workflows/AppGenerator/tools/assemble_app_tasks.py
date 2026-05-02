from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field

from .assembly_phase import assemble_features


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
        app_id = context_variables.get("app_id")
        raw_inject_key = context_variables.get("_mfj_resume_inject_as")
        if isinstance(raw_inject_key, str) and raw_inject_key.strip():
            inject_key = raw_inject_key.strip()
        else:
            inject_key = "mfj_app_task_results"

        merged = context_variables.get(inject_key)
        if not isinstance(merged, dict) and inject_key != "mfj_app_task_results":
            # Backstop for legacy prompts/tools that still reference the historical key.
            merged = context_variables.get("mfj_app_task_results")
            inject_key = "mfj_app_task_results"
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
