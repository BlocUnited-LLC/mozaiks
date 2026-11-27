"""Manifest-backed DesignDocs and subscription records for Factory export tests."""

import hashlib

from factory_app.workflows.DesignDocs.tools.save_design_doc import _experience_spec_to_yaml
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes


def summary_record(family, payload, *, app="app", user="owner"):
    producer = {"design_docs": "DesignDocs", "subscription_contract": "SubscriptionContractDesigner"}[family]
    data = _summary_bytes(payload)
    return BuildRecord(
        id=f"draft-{family}", app_id=app, build_family=family, build_key=family, version_number=2,
        lineage_root_id=f"current-{family}", lifecycle_status="draft", source_workflow=producer,
        source_chat_id=f"chat-{producer}", commit_metadata={
            "source_workflow": producer, "source_chat_id": f"chat-{producer}", "author_user_id": user,
            "metadata": {"summary_payload": payload},
        }, files_manifest=[{"path": f"{family}.json", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    )


def draft_inputs(*, app="app", user="owner", build="build"):
    experience, surface_map = {"pages": []}, {"surfaces": []}
    design = {"backend_markdown": "Reviewed draft", "experience_spec": experience, "surface_map": surface_map,
              "access_policy_review": {"status": "approved", "target_app_id": app, "build_id": build, "reviewed_by": user}}
    subscription = {"contract_required": False, "rationale": "Reviewed draft"}
    records = [summary_record("design_docs", design, app=app, user=user),
               summary_record("subscription_contract", subscription, app=app, user=user)]
    records[1].canonical_inputs_version["design_docs"] = records[0].id
    context = {"run_build_binding": {"target_app_id": app, "build_id": build, "build_registry_id": "reg", "phase": "refinement"},
               "workflow_sequence": "full_rebuild", "design_surface_map": surface_map,
               "backend_design_document": "Reviewed draft", "experience_spec_document": _experience_spec_to_yaml(experience, surface_map),
               "subscription_contract": subscription, **{f"{row.build_family}_artifact_version_id": row.id for row in records}}
    return {row.id: row for row in records}, context
