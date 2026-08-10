# Hosted App Composition Contract

Mozaiks application workspaces may compose the public Studio host from an
app-local FastAPI entrypoint.

The supported pattern is:

```python
from importlib import import_module

# Configure workspace environment before importing the Studio host.
studio_app = import_module("mozaiksai.hosts.studio")
app = studio_app.app

# App-local hosts may register product-specific routes on `app`.
```

The app-local host must configure workspace paths before importing
`mozaiksai.hosts.studio`:

- `MOZAIKS_APP_WORKSPACE_PATH`
- `PLATFORM_PATH`
- `MOZAIKS_WORKFLOWS_PATH` when the workspace owns a `workflows/` root
- `MOZAIKS_GENERATED_ARTIFACTS_PATH` when generated artifacts are workspace-local
- `MOZAIKS_BUILD_CONTEXT_PATH` when the workspace owns `build_context/`
- `RUNTIME_PLATFORM_EXTENSIONS` when the app provides platform extension hooks

The composed application may add app-owned routes, middleware, and operational
endpoints after importing `mozaiksai.hosts.studio:app`. It must not mutate
runtime internals, replace executor registries, or import private helper
functions from the Studio or Platform hosts.

Generic framework behavior needed by a hosted app should be exposed through a
stable public Mozaiks contract. Proprietary provider execution, production
credentials, money movement, hosted operations, and cross-customer intelligence
remain app/operator-layer behavior.

## Public Framework Contracts

Canonical applications may depend on these public framework seams:

- `mozaiksai.hosts.studio:app` for Studio host composition.
- `mozaiksai.core.studio:resolve_studio_scope` for app/user Studio scope.
- `mozaiksai.core.runtime.composition:PlatformExtensionBundle` for typed
  platform extension hooks.
- `mozaiksai.core.runtime.composition:dispatch_module_action` for app-local
  module dispatch with concrete permissions.
- `mozaiksai.core.validation:validate_generated_app_bundle` for generated app
  bundle validation.

Dispatch authority and event provenance are framework evidence only. They
explain why the framework allowed dispatch or where an event/reaction came
from; they do not authorize production infrastructure, DNS, payments, secrets,
credentials, or other operator state. Ordinary public app-local dispatch must
not expose trusted bypass through omitted permissions.
