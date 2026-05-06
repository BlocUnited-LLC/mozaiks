# mozaiksai — Runtime Substrate

`mozaiksai` is the reusable AI execution substrate in the four-host Mozaiks
architecture.

Canonical host entrypoints live in `mozaiksai/hosts/`:

- `mozaiksai/hosts/runtime.py` — runtime substrate host
- `mozaiksai/hosts/platform.py` — headless app host layered on the runtime
- `mozaiksai/hosts/studio.py` — local/private Studio management host layered on the platform
- `mozaiksai/hosts/mozaiks.py` — hosted product host layered on Studio

Start via the CLI:

```bash
mozaiks serve .               # platform host (default)
mozaiks serve . --host studio # Studio management host
```

This package owns the runtime layer only. It does not own page serving, shell
composition, Studio routes, or hosted product behavior.

## What This Layer Owns

- Executes AI agent conversations (AG2 GroupChat)
- Streams events to frontend via WebSocket
- Persists chat sessions to MongoDB
- Handles tool calls from agents
- Manages workflow state (in-progress, completed)
- Token accounting and observability

It must not own:

- shell config or page serving
- app admin shell composition
- Studio management/create routes
- hosted product behavior
- repo-specific CLI conveniences

## Directory Structure

```
mozaiksai/
├── core/
│   ├── adapters/         # Engine-specific orchestration adapters
│   ├── admin/            # Runtime/operator admin APIs
│   ├── auth/             # JWT validation and WebSocket auth
│   ├── data/             # Runtime persistence and storage helpers
│   ├── events/           # Runtime event dispatch and envelopes
│   ├── multitenant/      # app_id/user_id/chat_id scoping
│   ├── observability/    # Performance tracking and token logging
│   ├── ports/            # Engine-agnostic contracts
│   ├── runtime/          # App/runtime loading and composition helpers
│   ├── session/          # Session lifecycle helpers
│   ├── tokens/           # Token accounting
│   ├── transport/        # WebSocket transport and session registry
│   └── workflow/         # Workflow execution patterns and context management
```

## Key Entry Points

| File | Purpose |
|------|---------|
| `core/workflow/orchestration_patterns.py` | Main execution: `run_workflow_orchestration()` |
| `core/ports/orchestration.py` | Engine-agnostic contract: `OrchestrationPort` |
| `core/transport/simple_transport.py` | WebSocket manager: `SimpleTransport` |
| `core/events/unified_event_dispatcher.py` | Event routing: `UnifiedEventDispatcher` |

## Event System (3 Layers)

This runtime has THREE distinct event systems. Don't confuse them.

### 1. Business Events (Observability)
```python
emit_business_event("WORKFLOW_STARTED", "description", context={...})
```
Used for logging, metrics, system lifecycle.

### 2. UI Tool Events (Agent → UI)
```python
emit_tool_call_request("api_key_input", payload={...}, workflow_name="...")
```
Used for interactive UI components triggered by agents.

### 3. AG2 Runtime Events (Workflow Execution)
```
chat.text, chat.tool_call, chat.run_complete
```
Streamed via WebSocket during workflow execution.

`chat.run_complete` is the end-of-slice event, not blindly terminal completion.
Its payload status is authoritative:
- `status=1` means the workflow finished and the chat is completed
- `status=0` means the workflow paused awaiting another human turn and remains resumable

## How Execution Works

```
WebSocket message received
    ↓
SimpleTransport.receive() parses message
    ↓
run_workflow_orchestration(workflow_name, chat_id, ...)
    ↓
Load workflow config from factory_app/workflows/{name}/
    ↓
Create AG2 pattern (agents, handoffs, tools)
    ↓
AG2 GroupChat.a_run_group_chat()
    ↓
Stream events via SimpleTransport.broadcast_event()
    ↓
Persist session to MongoDB
```

## Multi-Tenant Isolation

Every operation is scoped by:
- `app_id` — Tenant identifier (required)
- `user_id` — User within tenant (required)
- `chat_id` — Conversation/run identifier (required)

## Engine-Agnostic Boundary

The `OrchestrationPort` protocol in `core/ports/orchestration.py` is the boundary between the runtime and AG2. All AG2-specific code lives in `core/adapters/`. Everything above the port is engine-agnostic.

```python
class OrchestrationPort(Protocol):
    async def run(request: RunRequest) -> RunResult
    async def resume(request: ResumeRequest) -> RunResult
    async def cancel(run_id: str) -> None
```

## Workflows Are Declarative

Workflows are defined in `factory_app/workflows/{name}/`:
- `orchestrator.yaml` — Workflow metadata and triggers
- `agents.yaml` — Agent definitions
- `handoffs.yaml` — Agent routing rules
- `context_variables.yaml` — Shared workflow state
- `tools.yaml` — Tool bindings
- `tools/*.py` — Tool implementations

Don't hardcode workflow behavior in this runtime.
When running from the canonical repo checkout without an active app workspace,
workflow discovery falls back to the shared `factory_app/workflows/` root.

## Live Smoke Workflows

For real AG2 acceptance coverage without running the full app build sequence, use
the shared smoke workflows under `factory_app/workflows/`:

- `RuntimeSmoke` validates base orchestration, streaming, persistence, and structured output.
- `RuntimeToolCallSmoke` validates the response-required workflow UI lane:
  `chat.tool_call -> tool_call_response`.
- `WorkflowPrimitiveAcceptance` validates the three canonical generated-workflow
  UI lanes together:
  - composer reply
  - structured inline workflow primitive
  - artifact workflow surface
- `AgentGenerator` is the stable real-AG2 regression target for multi-turn
  workflow generation, workflow-local UI, shipped workflow primitives, and final
  bundle handoff.

Run them through the live harness:

```bash
python scripts/run_live_mfj_smoke.py --workflow RuntimeSmoke --workflows-root factory_app/workflows
python scripts/run_live_mfj_smoke.py --workflow RuntimeToolCallSmoke --workflows-root factory_app/workflows --tool-response-text approved
python scripts/run_live_mfj_smoke.py --workflow WorkflowPrimitiveAcceptance --workflows-root factory_app/workflows --tool-response-file factory_app/workflows/WorkflowPrimitiveAcceptance/smoke_responses.json
python scripts/run_live_mfj_smoke.py --workflow AgentGenerator --workflows-root factory_app/workflows --prompt-file factory_app/workflows/AgentGenerator/smoke_prompt.txt --tool-response-file factory_app/workflows/AgentGenerator/smoke_responses.json --timeout-seconds 300
```

For real multi-turn workflows that pause on AG2 input requests, the same
harness can answer the canonical response-required lane with scripted replies:

```bash
python scripts/run_live_mfj_smoke.py --workflow ValueEngine --workflows-root factory_app/workflows ^
  --prompt "I want to build a task prioritization app for independent consultants." ^
  --user-reply "Independent consultants juggling multiple client deadlines." ^
  --user-reply "The biggest pain is deciding what to work on each morning." ^
  --user-reply "Current tools are Notion, spreadsheets, and Slack reminders." ^
  --user-reply "Approved. Proceed."
```

`--user-reply` values are consumed by pending `chat.tool_call` events with
`interaction_type=input_request` and by `chat.awaiting_reply` / non-terminal
`chat.run_complete` workflow pauses that hand control back to the composer
without an explicit UI tool. They do not send speculative free-form workflow
chat messages. AG2 compatibility prompts such as
`Please give feedback to chat_manager...` still use the pending input-request
lane; the runtime suppresses the raw prompt text. If AG2 emits a bare generic
feedback prompt immediately after a real user reply, the runtime auto-resumes
internally instead of surfacing a second fake user-facing pause. In `chat-ui`,
generic text input requests default to the main composer (`display=composer`),
and raw AG2 user-handoff pauses now surface as `chat.awaiting_reply`.

When a workflow also needs structured tool responses, prefer
`--tool-response-file` over ad hoc text fallbacks. The file is a JSON object:

```json
{
  "input_replies": [
    "We need an approval dashboard for regional operations teams."
  ],
  "default_input_reply": "Proceed with the proposed workflow.",
  "assistant_reply_rules": [
    {
      "contains": "final tweaks",
      "reply": "No final tweaks. Proceed to workflow planning."
    }
  ],
  "tool_responses": {
    "ApprovalCard": {
      "action": "approve",
      "approved": true,
      "rationale": "The checkpoint is clear."
    }
  }
}
```

`tool_responses` keys match the emitted workflow `tool_name` / `component_type`.
`default_input_reply` is optional and keeps variable-length AG2 interview flows
from stalling once the explicit `input_replies` list is exhausted.
`assistant_reply_rules` are optional and let the smoke harness answer specific
workflow stages based on the latest visible assistant message instead of blindly
consuming the next queued reply.

For a checked-in real-AG2 regression target, use the workflow-owned smoke pair:

- `factory_app/workflows/AgentGenerator/smoke_prompt.txt`
- `factory_app/workflows/AgentGenerator/smoke_responses.json`

Together they define the canonical AgentGenerator regression target for:

- multi-turn AG2 interview replies
- diagram artifact review via the composer reply lane
- shipped/shared workflow components
- final download handoff

The smoke harness also performs one runtime-level resume pass if a run lands in
a persisted generic AG2 feedback pause after the visible interaction is already
complete.

Both require a working `.env` with `OPENAI_API_KEY` and `MONGO_URI`, plus a reachable MongoDB instance.
If you rely on the repo Docker Compose service, Docker Desktop must be running before
`docker compose -f infra/compose/docker-compose.yml up -d mongo`.
