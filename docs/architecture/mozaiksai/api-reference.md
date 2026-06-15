# API Reference

Current endpoint surface for the Mozaiks runtime and platform hosts.
These are the actual routes registered in `mozaiksai/hosts/runtime.py` and
`mozaiksai/hosts/platform.py`. Use this as a quick orientation — for
behavioral contracts, see the linked architecture docs.

## Hosts

| Host module | Default port | Primary purpose |
|---|---|---|
| `mozaiksai.hosts.runtime` | 8000 | Minimal chat + health routes only |
| `mozaiksai.hosts.platform` | 8000 | Full app platform: modules, pages, sessions, profile |
| `mozaiksai.hosts.studio` | 8000 | Studio management layer; composes platform host |

Start with `mozaiks serve . --host studio` or `uvicorn mozaiksai.hosts.studio:app --reload`
for the recommended local development surface.

---

## Health

All health routes are on the runtime host and are inherited by platform/Studio.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health/live` | Liveness probe — always 200 if process is running |
| `GET` | `/api/health/ready` | Readiness probe — returns 503 with `app_startup: degraded: <reason>` if startup failed |
| `GET` | `/api/health` | Combined health summary with event metrics |
| `GET` | `/health/active-runs` | Active workflow run count |

### Startup Degradation

When the platform host catches a module load error at startup, it sets
`app.state.startup_degraded = True` and records the reason. The readiness
endpoint returns HTTP 503 so load balancers and healthchecks can detect a
partial startup without the process crashing.

---

## Workflow Discovery

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/workflows` | List registered workflows |
| `GET` | `/api/workflows/config` | Workflow config for frontend shell |
| `GET` | `/api/workflows/{workflow_name}/transport` | WebSocket transport metadata |
| `GET` | `/api/workflows/{workflow_name}/tools` | All tools for a workflow |
| `GET` | `/api/workflows/{workflow_name}/ui-tools` | UI-facing tools only |

---

## Chat and Sessions

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chats/{app_id}/{workflow_name}/start` | Start a new chat session |
| `POST` | `/api/workflows/{workflow_name}/trigger` | Trigger a workflow by event or external input |
| `POST` | `/chat/{app_id}/{chat_id}/{user_id}/input` | Submit a user message to an active run |
| `POST` | `/chat/{app_id}/{chat_id}/component_action` | Dispatch a UI component action |
| `POST` | `/api/tool-call/respond` | Respond to a pending human-in-the-loop tool call |

### Session History (platform host only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/chats/{app_id}/{workflow_name}` | List chats for a workflow |
| `GET` | `/api/chats/exists/{app_id}/{workflow_name}/{chat_id}` | Check if a chat exists |
| `GET` | `/api/sessions/list/{app_id}/{user_id}` | List sessions for a user |
| `GET` | `/api/sessions/recent/{app_id}/{user_id}` | Most recent sessions |
| `GET` | `/api/sessions/oldest/{app_id}/{user_id}` | Oldest sessions |
| `DELETE` | `/api/sessions/{app_id}/{user_id}` | Delete user sessions |
| `DELETE` | `/api/general_chats/{app_id}/{user_id}` | Delete general chats |

---

## Modules and Actions (platform host only)

Module actions are dispatched through a single generic route pair. The module
name and action name come from `module.yaml` declarations.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/modules/{module_name}/{action_name}` | Dispatch a read action |
| `POST` | `/api/modules/{module_name}/{action_name}` | Dispatch a write action |

Modules may also register custom FastAPI routers via `runtime_extensions.yaml`
with `kind: api_router`. Those routes are mounted at the declared `prefix`.

---

## Transitions (platform host only)

Transitions are the named workflow hand-off points defined in
`extension_registry.json`. The platform host surfaces them so the frontend
shell can navigate the build journey.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/transitions/{transition_id}` | Get a named transition definition |
| `POST` | `/api/transitions/resolve` | Resolve the current transition state |

---

## Session Decisions (platform host only)

Human-in-the-loop decision points for multi-step workflows.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/session/state` | Current session decision state |
| `POST` | `/api/session/decisions/pending` | List pending decisions |
| `POST` | `/api/session/decisions/resolve` | Resolve a pending decision |

---

## App Shell and Pages (platform host only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/shell-config` | App shell config for the frontend |
| `GET` | `/api/theme-config` | Active theme config |
| `GET` | `/api/themes/{app_id}` | Theme config for a specific app |
| `GET` | `/api/pages/{name}` | Page definition by name |

---

## User Profile (platform host only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/me` | Current user profile |
| `PUT` | `/api/me` | Update current user profile |
| `GET` | `/api/me/preferences` | User preferences |
| `PUT` | `/api/me/preferences` | Update user preferences |
| `GET` | `/api/me/usage` | Token usage ledger for the current user |
| `GET` | `/api/me/tokens` | Token wallet balances for the current user |
| `POST` | `/api/me/tokens/sync` | Idempotently materialize current subscription token allowances |
| `GET` | `/api/me/tokens/ledger` | Token wallet ledger entries for the current user |
| `GET` | `/api/me/profile-panels` | Module-declared profile panel contributions |

---

## File Upload (platform host only)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chat/upload` | Upload a file for chat context |
| `POST` | `/api/chat/upload/{app_id}/{user_id}` | Upload scoped to an app/user |

---

## Event Metrics

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/events/metrics` | In-process event bus metrics (runtime host) |

---

## Related Architecture

- [Runtime Architecture](../../architecture/foundations/runtime-overview.md)
- [Transport and Streaming](transport-and-streaming.md)
- [Token Management](token-management.md)
- [Module System](../../architecture/app/canonical-app-structure.md)
