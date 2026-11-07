"""Materialize typed model-context statusbars into workflow middleware YAML."""

from __future__ import annotations

from typing import Any

import yaml

from mozaiksai.core.workflow.declarative.contracts import (
    StatusbarMiddlewareSpec,
    parse_middleware_config,
)


def materialize_workflow_statusbar(entry: dict[str, Any]) -> dict[str, Any]:
    if "statusbar_middleware" not in entry:
        return entry
    raw_specs = entry["statusbar_middleware"]
    if not isinstance(raw_specs, list):
        raise ValueError("statusbar_middleware must be a list")
    specs = [StatusbarMiddlewareSpec.model_validate(spec).model_dump() for spec in raw_specs]
    files = {file["filename"]: file for file in entry["files"]}
    if len(files) != len(entry["files"]):
        raise ValueError("workflow files contain duplicate filenames")
    if "middleware.yaml" not in files:
        raise ValueError("statusbar materialization requires middleware.yaml")
    payload = yaml.safe_load(files["middleware.yaml"]["content"])
    if not isinstance(payload, dict):
        raise ValueError("middleware.yaml must contain a mapping")
    if "statusbar_middleware" in payload and payload["statusbar_middleware"] != specs:
        raise ValueError("statusbar_middleware conflicts with an existing middleware.yaml declaration")
    payload["statusbar_middleware"] = specs
    parse_middleware_config(payload)
    content = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return {
        **entry,
        "files": [
            {**file, "content": content} if file["filename"] == "middleware.yaml" else file
            for file in entry["files"]
        ],
    }
