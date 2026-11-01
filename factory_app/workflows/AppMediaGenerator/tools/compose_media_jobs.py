"""Turn the validated art direction into one image job per planned asset."""

from __future__ import annotations

from typing import Annotated, Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

# Pages render their own copy; text inside an image invents facts such as prices or names.
IMAGE_RULES = (
    "Render no visible text anywhere: no words, letters, numbers, prices, signage, "
    "labels, logos, or watermarks. Boards, signs, cups, and packaging stay blank."
)
_MAKER_BY_ASPECT = {
    "square": "SquareImageMakerAgent",
    "landscape": "LandscapeImageMakerAgent",
    "portrait": "PortraitImageMakerAgent",
}


def compose_media_jobs(
    *,
    MediaArtDirection: Annotated[dict[str, Any] | None, "Validated art direction"] = None,
    context_variables: Annotated[Any | None, "Runtime context"] = None,
) -> dict[str, Any]:
    if context_variables is None:
        raise ValueError("Composing media jobs requires runtime context")
    models, _ = load_workflow_structured_outputs("AppMediaGenerator")
    direction = models["MediaArtDirection"].model_validate(detach(MediaArtDirection)).model_dump(mode="json")
    requests = {
        item["asset_id"]: item
        for item in detach(context_variables.get("media_requests")) or []
        if isinstance(item, dict) and item.get("asset_id")
    }
    jobs: list[dict[str, Any]] = []
    ignored: list[str] = []
    for image in direction["images"]:
        request = requests.get(image["asset_id"])
        if request is None or any(job["task_id"] == image["asset_id"] for job in jobs):
            ignored.append(image["asset_id"])
            continue
        jobs.append({
            "task_id": request["asset_id"],
            "initial_agent": _MAKER_BY_ASPECT.get(request.get("aspect") or "", "SquareImageMakerAgent"),
            "initial_message": (
                f"Art direction: {direction['style_guide']}\n\n"
                f"Image: {image['prompt']}\n\n"
                f"Rules: {IMAGE_RULES}"
            ),
        })
    context_variables.set("media_job_items", jobs)
    return {"outcome": "composed", "job_count": len(jobs), "ignored_asset_ids": ignored}
