# Universal Orchestrator — Event Routing Architecture

> Defines how every event in the system — free-text, REST, webhook, scheduled —
> gets routed to the right GroupChat or process runner.
> This is process-independent: the same routing model applies to the build pipeline,
> a future marketing process, a financial planning process, or anything else.

**Status note:** Treat this document as a routing design reference, not as proof that every path is live-validated in the current AG2 runtime. Correctness claims for runtime behavior should come from code and targeted smoke coverage, not from this document alone.

---

## Core Concept

The UniversalOrchestrator is **not an AI agent that thinks**. It is a router.
It receives events, determines which GroupChat or runner should handle them,
and dispatches. It never generates content.

The key insight: **the trigger type is the classification when the trigger is explicit**.
Classification (LLM call) is only needed when the trigger is ambiguous free-text.

```
ALL EVENTS IN THE SYSTEM
        ↓
[ UniversalOrchestrator ]
        ↓
  Is the trigger explicit/structured?
  ├── YES → route directly (no LLM needed)
  └── NO  → run ChangeClassifier (~1s LLM call) → then route
        ↓
  Target: GroupChat | ProcessRunner | REST handler
```

---

## Event Namespaces

Every event has a **trigger type** and a **target**. The orchestrator maps one to the other.

### Trigger Types

| Trigger Type | Example | Structured? | How Routed |
|---|---|---|---|
| **User free-text (chat)** | "I want video streaming instead" | ❌ Ambiguous | ChangeClassifier → target |
| **User REST action** | `PATCH /settings { tech_stack: "vue" }` | ✅ Explicit | Direct → DecompositionGroupChat |
| **User button / UI event** | Click "Deploy" | ✅ Explicit | Direct → DeploymentGroupChat |
| **User tier change** | Subscription upgraded to Pro | ✅ Explicit | Direct → ValueEngineGroupChat (suggest features) |
| **Inbound webhook** | Stripe payment confirmed | ✅ Explicit | Direct → ProcessRunner (no AI needed) |
| **Scheduled / cron** | Daily sandbox health check | ✅ Explicit | Direct → ProcessRunner |
| **System event** | Build DAG completed | ✅ Explicit | Direct → E2B sandbox provisioner |
| **Incoming webhook (ambiguous payload)** | Slack message forwarded from user | ❌ Ambiguous | Lightweight classifier → target |

### Routing Rule

```
if event.is_structured:
    target = EVENT_ROUTE_MAP[event.type]
    dispatch(target, event.payload)
else:
    change_type = ChangeClassifier.classify(event.text, current_context)
    target = CHANGE_TYPE_ROUTE_MAP[change_type]
    dispatch(target, event.text, change_type)
```

---

## The Event Route Map (explicit triggers)

These are hard-coded because the intent is unambiguous.

```python
EVENT_ROUTE_MAP = {
    # Build pipeline
    "ui.build_confirmed":           "DecompositionGroupChat",
    "ui.deploy_clicked":            "DeploymentGroupChat",
    "ui.sandbox_extend":            "e2b.extend_sandbox",          # no AI
    "ui.sandbox_kill":              "e2b.kill_sandbox",            # no AI

    # Settings changes that affect the built app
    "rest.settings.tech_stack":     "DecompositionGroupChat",      # full re-decompose
    "rest.settings.db_type":        "DecompositionGroupChat",      # affects schema tasks
    "rest.settings.app_name":       "PatchAgent",                  # surface rename

    # Account / subscription
    "rest.subscription.upgraded":   "ValueEngineGroupChat",        # suggest expanded features
    "rest.subscription.downgraded": "ValueEngineGroupChat",        # trim features to tier

    # System / lifecycle
    "system.build_completed":       "e2b.run_preview",             # no AI
    "system.sandbox_expired":       "notify.user",                 # no AI
    "webhook.stripe.payment":       "rest.billing_handler",        # no AI
}
```

New event types are a one-line addition here. No new agents, no new GroupChats
unless the new event genuinely requires multi-agent reasoning.

---

## The Change Type Route Map (free-text triggers)

When the trigger is free-text, the ChangeClassifier produces a `ChangeType` enum,
which maps to a target:

```python
CHANGE_TYPE_ROUTE_MAP = {
    "FOUNDATIONAL": "ValueEngineGroupChat",
    "STRUCTURAL":   "DecompositionGroupChat",
    "FEATURE":      "FeedbackGroupChat",
    "SURFACE":      "FeedbackGroupChat",
}
```

ChangeType definitions are **process-specific** — the build pipeline's FOUNDATIONAL
means something different from a marketing plan's FOUNDATIONAL. Each process registers
its own ChangeType definitions and classifier prompt. The routing infrastructure is shared.

For app-build refinement, this generic classifier layer should feed a stricter
control-plane taxonomy (`patch`, `design`, `feature`, `core`) before choosing a
re-entry point. The detailed refinement-routing plan is internal; the public
contract is that refinement classification happens before workflow re-entry.

---

## The ChangeClassifier

A single LLM call (~1 second). Not a GroupChat — just a function.
Takes free-text + current process context, returns a ChangeType + rationale.

**Implementation (AG2 `StringLLMCondition`):**

```python
from autogen.agentchat.group.conditions import StringLLMCondition

# One condition per ChangeType. Evaluated highest-severity first.
foundational_condition = StringLLMCondition(
    prompt=(
        "The user submitted this message: '{user_text}'\n"
        "The current process state: {process_context_summary}\n\n"
        "Does this require restarting the entire process from the beginning, "
        "changing the fundamental goal, or invalidating >50% of prior work?\n"
        "Answer only YES or NO."
    )
)

structural_condition = StringLLMCondition(
    prompt=(
        "The user submitted this message: '{user_text}'\n"
        "Current domains / modules: {domains}\n\n"
        "Does this add, remove, or fundamentally rename a major domain or module "
        "without changing the overall goal?\n"
        "Answer only YES or NO."
    )
)

# FEATURE and SURFACE: apply only if FOUNDATIONAL and STRUCTURAL are NO
```

Evaluation order: FOUNDATIONAL → STRUCTURAL → FEATURE → SURFACE (first YES wins).
This prevents a structural change from being mislabeled as FEATURE.

---

## The UniversalOrchestrator — AG2 Pattern

The UniversalOrchestrator is the single entry point the frontend WebSocket talks to.
It holds the routing tables and dispatches. It is always running while the user is active.

```
UniversalOrchestrator
    │
    ├── [explicit event] → EVENT_ROUTE_MAP lookup → direct dispatch
    │
    ├── [free-text] → ChangeClassifier → CHANGE_TYPE_ROUTE_MAP → dispatch
    │
    ├── handoff → ValueEngineGroupChat      (FOUNDATIONAL / subscription changes)
    │
    ├── handoff → DecompositionGroupChat    (STRUCTURAL / tech_stack settings)
    │
    ├── handoff → FeedbackGroupChat         (FEATURE / SURFACE free-text)
    │     ├── FeedbackRouterAgent
    │     ├── PatchAgent
    │     └── partial DAGExecutor re-run
    │
    └── handoff → DeploymentGroupChat       (deploy button / deploy confirmed)
```

The orchestrator never generates content. If it handles an event, it's because
the event maps to a non-AI runner (E2B, billing handler, notification). If the event
needs reasoning, it immediately hands off to the right GroupChat.

---

## Adding a New Process (Future)

When a second process is added (e.g. MarketingGroupChat), the additions are:
1. New GroupChat with its own agents
2. New ChangeType definitions + classifier prompt registered for that process
3. New entries in `EVENT_ROUTE_MAP` for that process's explicit triggers
4. New entry in `CHANGE_TYPE_ROUTE_MAP` for that process

The UniversalOrchestrator itself does not change. The routing tables grow.

Example: Marketing plan process
```
"marketing.user_windfall":      ChangeClassifier (FOUNDATIONAL) → MarketingGroupChat (restart)
"marketing.budget_changed":     ChangeClassifier (STRUCTURAL)   → MarketingGroupChat (revise plan)
"marketing.copy_tweak":         ChangeClassifier (SURFACE)      → PatchAgent
"rest.marketing.budget_set":    ❌ explicit                     → Direct to MarketingGroupChat
```

The pattern is identical. Generalize only when this second process actually exists.

---

## AG2 Runtime — What We Use vs What We Build

The UniversalOrchestrator is not hand-rolled infrastructure. It is built on AG2's
native runtime primitives. Do not build anything AG2 already provides.

### AG2 provides (do not reimplement):

**Event streaming** — `a_run_group_chat_iter()` with `yield_on=[GroupChatRunChatEvent]`
emits one event per agent turn. The WebSocket just forwards these. No manual emission needed.

**Parallel task execution** — `a_run_iter()` is async. Independent DAG tasks run
concurrently via `asyncio.gather()` over multiple `a_run_iter()` calls. One gather
per dependency layer. When the gather resolves, all dependencies are met and the
next layer fires.

**Custom events** — `IOStream.get_default().send(DAGTaskCompletedEvent(...))` inside
a task tool lets the DAGExecutor receive typed signals per task completion.
The WebSocket build stepper receives these directly.

**Pause / resume** — `GroupChatManager.resume(messages)` resumes a saved chat from
a prior state. Save the manager state to MongoDB → user reopens browser → reconnect
and resume. Solves session persistence without custom code.

**Per-agent context injection** — `UpdateSystemMessage` with `ContextVariables`
replaces our manual `_format_injected_context()`. The `ContextStore` becomes
AG2's `ContextVariables`, persisted automatically across every agent turn.

### We build (AG2 cannot do this):

**Topological sort + dependency layer grouping** — AG2 GroupChat is sequential
speaker turns. It has no concept of "task B depends on task A". The DAGExecutor
keeps its dependency resolution logic. What changes is that the actual agent runs
become `a_run_iter()` calls instead of hand-rolled asyncio tasks.

**The routing tables** — `EVENT_ROUTE_MAP` and `CHANGE_TYPE_ROUTE_MAP` are ours.
AG2 provides the handoff mechanism; we define what maps to what.

### The revised DAGExecutor model:

**Custom event definition** (replaces manual ContextStore signals):
```python
from autogen.events.base_event import BaseEvent, wrap_event
from autogen.io.base import IOStream

@wrap_event
class DAGTaskCompletedEvent(BaseEvent):
    """Emitted by a task agent tool when it finishes writing its output file."""
    task_id: str
    task_type: str
    domain: str
    output_files: list[str]
    duration_seconds: float

    def print(self, f=None):
        f = resolve_print_callable(f)
        f(f"[DAG] {self.task_type} ({self.domain}) completed in {self.duration_seconds:.1f}s")
```

**Emitted from inside the task tool** (not from DAGExecutor):
```python
# Inside write_output_files() tool that every task agent calls:
IOStream.get_default().send(
    DAGTaskCompletedEvent(
        task_id=task.task_id,
        task_type=task.task_type,
        domain=task.domain,
        output_files=written_files,
        duration_seconds=elapsed,
    )
)
```

**DAGExecutor parallel layer execution:**
```python
async def _run_layer(self, tasks: list[DAGTask]) -> list[DAGTaskCompletedEvent]:
    """Run all tasks in a dependency layer concurrently."""
    async def consume(task):
        async for event in task_agent.a_run_iter(
            message=build_prompt(task),
            yield_on=[DAGTaskCompletedEvent],
        ):
            if isinstance(event, DAGTaskCompletedEvent):
                # inject output into ContextVariables for dependent tasks
                self.context[event.content.task_id] = event.content
                # forward to WebSocket → build stepper UI updates live
                await self.websocket.send(event.content)
                return event.content
    return await asyncio.gather(*[consume(t) for t in tasks])

# Main execution loop — replaces ~100 lines of current dag_executor.py:
for layer in topological_layers(self.dag):
    await self._run_layer(layer)
# When gather resolves → all deps met → next layer fires automatically
```

**WebSocket build stepper receives `DAGTaskCompletedEvent` per task:**
```
Billing Schema     ✓  2.3s     ← DAGTaskCompletedEvent fired
Billing Config     ✓  4.1s     ← DAGTaskCompletedEvent fired
Billing Models     ⟳  running  ← a_run_iter in progress
Auth Schema        ✓  1.9s     ← ran in parallel with Billing Schema
```

---

## DAGExecutor Refactor Spec

> **When to do this:** After the current `dag_executor.py` runs end-to-end at least once
> and `FeedbackGroupChat` + `DAGTask.amendment` are complete. Do not refactor before
> the pipeline produces correct output. Refactor to AG2 primitives after correctness is proven.

### Files that change

| File | Action |
|---|---|
| `build_pipeline/execution/dag_executor.py` | Rewrite `_execute_task()` to use `a_run_iter()`. Keep `topological_layers()`. Delete manual asyncio task management. |
| `build_pipeline/execution/agent_runner.py` | Delete `_format_injected_context()`. Replace with `UpdateSystemMessage` + `ContextVariables`. |
| `build_pipeline/core/dag_schema.py` | Delete `ContextStore` class. `ContextVariables` from AG2 takes its place. |
| `build_pipeline/core/events.py` | **NEW FILE.** Define all custom events: `DAGTaskCompletedEvent`, `DAGLayerCompletedEvent`, `BuildCompletedEvent`. |
| `build_pipeline/runner.py` | Replace `BuildRunResult` WebSocket emission with `yield_on=[DAGTaskCompletedEvent]` forwarded to WebSocket. |

### Files that do NOT change

- `build_pipeline/decomposition/groupchat.py` — unchanged, already correct AG2 GroupChat
- `build_pipeline/core/dag_schema.py` (DAGTask, BuildDAG) — schema contracts stay
- `build_pipeline/execution/task_role_registry.py` — system prompts stay
- `build_pipeline/preview/e2b_sandbox.py` — unchanged

### New file: `build_pipeline/core/events.py`

```python
from typing import Any, Callable
from autogen.events.base_event import BaseEvent, wrap_event, resolve_print_callable

@wrap_event
class DAGTaskCompletedEvent(BaseEvent):
    """Emitted by a task agent's write_output tool when it finishes.
    DAGExecutor yields on this to know when to advance the DAG.
    WebSocket forwards this to the frontend build stepper.
    """
    task_id: str
    task_type: str
    domain: str
    output_files: list[str]
    duration_seconds: float
    context_key: str           # key under which output is stored in ContextVariables

    def print(self, f: Callable[..., Any] | None = None) -> None:
        f = resolve_print_callable(f)
        f(f"[DAG] {self.task_type} ({self.domain}) ✓ {self.duration_seconds:.1f}s", flush=True)


@wrap_event
class DAGLayerCompletedEvent(BaseEvent):
    """Emitted by DAGExecutor when all tasks in a dependency layer finish.
    Not forwarded to WebSocket — internal signal only.
    """
    layer_index: int
    task_ids: list[str]

    def print(self, f: Callable[..., Any] | None = None) -> None:
        f = resolve_print_callable(f)
        f(f"[DAG] Layer {self.layer_index} complete ({len(self.task_ids)} tasks)", flush=True)


@wrap_event
class BuildCompletedEvent(BaseEvent):
    """Emitted by runner.py when the full DAG finishes.
    Triggers E2B sandbox provisioning automatically.
    """
    enterprise_id: str
    output_dir: str
    total_tasks: int
    total_duration_seconds: float

    def print(self, f: Callable[..., Any] | None = None) -> None:
        f = resolve_print_callable(f)
        f(f"[Build] Complete — {self.total_tasks} tasks in {self.total_duration_seconds:.0f}s", flush=True)
```

### Revised `dag_executor.py` core (post-refactor)

```python
from autogen.agentchat.group import ContextVariables
from build_pipeline.core.events import DAGTaskCompletedEvent, DAGLayerCompletedEvent
from autogen.io.base import IOStream

class DAGExecutor:
    def __init__(self, dag: BuildDAG, llm_config, output_dir, websocket=None):
        self.dag = dag
        self.llm_config = llm_config
        self.output_dir = output_dir
        self.websocket = websocket
        # AG2 ContextVariables replaces our hand-rolled ContextStore
        self.context = ContextVariables(data={})

    async def run(self) -> BuildRunResult:
        for layer_index, layer in enumerate(topological_layers(self.dag)):
            completed = await self._run_layer(layer_index, layer)
        return BuildRunResult(output_dir=self.output_dir, ...)

    async def _run_layer(self, layer_index: int, tasks: list[DAGTask]):
        async def consume(task: DAGTask):
            agent = self._build_agent(task)   # ConversableAgent with UpdateSystemMessage
            async for event in agent.a_run_iter(
                message=self._build_prompt(task),
                yield_on=[DAGTaskCompletedEvent],
            ):
                if isinstance(event, DAGTaskCompletedEvent):
                    # Store output for downstream tasks
                    self.context[event.content.context_key] = event.content
                    # Forward to WebSocket → build stepper UI
                    if self.websocket:
                        await self.websocket.send_json(event.content.__dict__)
                    return event.content

        results = await asyncio.gather(*[consume(t) for t in tasks])
        return results

    def _build_agent(self, task: DAGTask) -> ConversableAgent:
        role = _role_registry.get(task.task_type)
        return ConversableAgent(
            name=f"{task.task_type}_{task.domain}",
            llm_config=self.llm_config,
            # UpdateSystemMessage injects prior task outputs automatically
            update_agent_state_before_reply=[
                UpdateSystemMessage(role.base_prompt + self._context_template(task))
            ],
        )

    def _context_template(self, task: DAGTask) -> str:
        # Builds {context_key} slots from task.consumes_context
        # AG2 substitutes actual values from self.context before each reply
        return "\n".join(
            f"Prior output ({key}): {{{key}}}"
            for key in (task.consumes_context or [])
        )
```

### Session persistence via `GroupChatManager.resume()`

```python
# When user closes browser mid-build:
manager_state = groupchat_manager.export_state()   # AG2 built-in
await mongodb.save(enterprise_id, "session_state", manager_state)

# When user reopens:
state = await mongodb.load(enterprise_id, "session_state")
groupchat_manager.resume(messages=state)           # AG2 built-in — continues exactly where left off
```

This replaces Open Question #1 from BUILD_UX_ARCHITECTURE.md entirely.

---

## What This Is Not

- **Not a monolith.** The UniversalOrchestrator routes — it does not contain business logic.
- **Not process-specific.** The routing tables are configured per process. The engine is shared.
- **Not always AI.** Most REST events never touch an LLM. The orchestrator dispatches them
  to non-AI handlers (billing, notifications, E2B lifecycle). AI is invoked only when reasoning
  is required.
- **Not built yet.** This is an architectural spec. The current implementation has
  `DecompositionGroupChat` (built) and the build pipeline runner (built). Everything
  else in this doc is pending.

---

## Implementation Status

### UniversalOrchestrator (build after FeedbackGroupChat ships)

| Component | Status |
|---|---|
| `EVENT_ROUTE_MAP` | ❌ not built |
| `CHANGE_TYPE_ROUTE_MAP` | ❌ not built |
| `ChangeClassifier` function | ❌ not built |
| `UniversalOrchestrator` agent | ❌ not built |
| WebSocket ↔ Orchestrator bridge | ❌ not built |
| REST event → Orchestrator bridge | ❌ not built |

### DAGExecutor AG2 refactor (do after pipeline runs end-to-end)

| Component | Status |
|---|---|
| `build_pipeline/core/events.py` (custom events) | ❌ not built |
| `dag_executor.py` → `a_run_iter()` + `asyncio.gather()` | ❌ pending refactor |
| `agent_runner.py` → `UpdateSystemMessage` + `ContextVariables` | ❌ pending refactor |
| `dag_schema.py` → delete `ContextStore` | ❌ pending refactor |
| `runner.py` → WebSocket forwards `DAGTaskCompletedEvent` | ❌ pending refactor |
| Session persistence via `GroupChatManager.resume()` | ❌ not built |

> **Build order:**
> 1. `DAGTask.amendment` field in dag_schema.py
> 2. `FeedbackGroupChat` (ChangeClassifierAgent, FeedbackRouterAgent, PatchAgent)
> 3. `run_feedback_iteration()` in runner.py
> 4. DAGExecutor AG2 refactor (after pipeline runs end-to-end)
> 5. UniversalOrchestrator (after FeedbackGroupChat ships)

---

*Last updated: February 26, 2026*

