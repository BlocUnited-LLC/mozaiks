"""App-authored content probe inputs; the caller supplies its approved role.

This helper neither declares a role grant nor changes a pack's authorization.
ContentEntry's create/update/archive operations are custom actions, so mutation
targets come from their own prerequisite response, not an observer-owned CRUD
record. Each create omits slug and lets the server generate a unique one.
"""
from __future__ import annotations

import json

import yaml

from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
    SCHEMA_VERSION,
    parse_module_smoke_fixtures,
)


def content_smoke_fixtures(role_id: str) -> str:
    """Render bounded canonical YAML for a role the test app already grants."""
    def title(value):
        return {"field": "title", "source": "literal", "value_json": json.dumps(value)}

    fixtures = [{"action": "create_entry", "roles": [role_id], "setup": [],
                 "params": [title("Observer content entry")]}]
    for action in ("update_entry", "archive_entry"):
        params = [{"field": "entry_id", "source": "response", "step_id": "entry",
                   "response_path": ["entry", "entry_id"]}]
        if action == "update_entry":
            params.append(title("Observer revised content entry"))
            params.append({"field": "status", "source": "literal", "value_json": json.dumps("published")})
        fixtures.append({
            "action": action, "roles": [role_id],
            "setup": [{"id": "entry", "module": "content", "action": "create_entry",
                       "roles": [role_id], "params": [title("Observer mutation target")]}],
            "params": params,
        })
    document = {"schema_version": SCHEMA_VERSION, "fixtures": fixtures}
    parse_module_smoke_fixtures(document)
    return yaml.safe_dump(document, sort_keys=False)
