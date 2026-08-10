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
