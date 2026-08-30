# Canonical test apps

These workspaces are small, complete Mozaiks applications. They are not Studio
mock records and they are not generated into a temporary test directory. Each
one loads through the same `AppLoader`, module executor, page-schema loader, and
workflow discovery paths used by a standalone app.

| Workspace | Contract exercised |
| --- | --- |
| `project-hub` | Authenticated pages, persistent CRUD modules, and app data contracts |
| `reporting-saas` | Subscription plans, assignment storage, and an entitlement-gated action |
| `research-ops` | App-local workflow discovery, deterministic module actions, and an admin registry |

From the Mozaiks repository environment, run one app with:

```powershell
mozaiks serve .\examples\canonical-apps\project-hub --host platform --port 8000
```

Authenticated examples require the provider-neutral variables described by
their `.env.example` files. MongoDB is required for live persistence. The
acceptance suite uses deterministic in-memory test adapters and does not call a
paid model or external provider:

```powershell
pytest -q --no-cov tests/test_canonical_example_apps.py
```

These examples intentionally stay in the OSS repository as executable
reference/test assets. Customer apps produced for deployment still become
independent workspaces or repositories.
