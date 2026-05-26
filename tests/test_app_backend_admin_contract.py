from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_workspace(),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_app_backend_admin_contract_accepts_explicit_builtin_and_schema_panels() -> None:
    module_uri = (_workspace() / "chat-ui/src/admin/contracts/appAdminContract.js").resolve().as_uri()
    script = f"""
      import {{ parseAppBackendAdminConfig }} from {json.dumps(module_uri)};
      const result = parseAppBackendAdminConfig({{
        schema_version: 'mozaiks.admin.app_backend.v1',
        panels: [
          {{
            id: 'app.users',
            label: 'Users',
            section: 'users',
            renderer: 'builtin',
            builtin_panel: 'users'
          }},
          {{
            id: 'billing.summary',
            label: 'Billing',
            section: 'billing',
            renderer: 'schema',
            sections: [
              {{
                id: 'billing-table',
                primitive: 'DataTable',
                config: {{ api_endpoint: '/api/admin/billing', columns: [{{ key: 'plan', label: 'Plan' }}] }}
              }}
            ]
          }}
        ]
      }});
      console.log(JSON.stringify(result));
    """
    result = _run_node(script)

    assert result["issues"] == []
    assert len(result["panels"]) == 2
    assert result["panels"][0]["renderer"] == "builtin"
    assert result["panels"][0]["builtin_panel"] == "users"
    assert result["panels"][1]["renderer"] == "schema"
    assert result["panels"][1]["layout"] == "full-width"


def test_app_backend_admin_contract_rejects_removed_shapes() -> None:
    module_uri = (_workspace() / "chat-ui/src/admin/contracts/appAdminContract.js").resolve().as_uri()
    script = f"""
      import {{ parseAppBackendAdminConfig }} from {json.dumps(module_uri)};
      const result = parseAppBackendAdminConfig({{
        panels: [
          {{ id: 'stats', label: 'Stats', section: 'overview', renderer: 'builtin', builtin_panel: 'stats' }}
        ]
      }});
      console.log(JSON.stringify(result));
    """
    result = _run_node(script)

    assert result["panels"] == []
    assert result["issues"] == [
        "App backend admin config must declare schema_version=mozaiks.admin.app_backend.v1."
    ]
