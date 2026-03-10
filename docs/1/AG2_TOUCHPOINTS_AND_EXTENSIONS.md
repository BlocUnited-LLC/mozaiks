# Parallel Fan-Out / Fan-In in AG2 — A Production Use Case
---

## 1. The Problem

Consider a user request like: *"Build me a SaaS app with an auth module, a billing
module, and an API gateway."*

A single GroupChat can't handle this well. The context window bloats with three unrelated
domains, agent specialization breaks down when one GroupChat tries to cover auth *and*
billing *and* API design, and the work is inherently parallelizable — there's no reason
the billing module has to wait for the auth module to finish.

The natural answer is to split the request into N independent GroupChats (one per module)
that run **concurrently**, then merge their results back into the original conversation.

AG2's existing `NestedChat` doesn't solve this. Nested chats are **sequential** — one
inner chat runs to completion before the next begins — and they don't support aggregating
results from multiple children back into the parent's `ContextVariables`.

What we need is for a running GroupChat to determine *at runtime* that it should fork into
N parallel child GroupChats, wait for all of them to finish, collect their outputs, and
resume where it left off. We call this **mid-flight decomposition** — the decision to
parallelize happens mid-conversation, not at configuration time.

Concretely, the system must:

1. **Pause** the running parent GroupChat
2. **Fan out** N independent child GroupChats in parallel
3. **Wait** for all N to complete
4. **Aggregate** the results from each child
5. **Resume** the parent GroupChat with the aggregated context

AG2 does not support any of steps 1–5 natively. The rest of this document explains why,
what we built to make it work, and how the proposed streaming API redesign would change things.

---

## 2. Why AG2 Cannot Do This Natively

### 2.1 `generate_reply()` Is the Atomic Unit — There Is No Escape Hatch Mid-Loop

AG2's `GroupChatManager` drives conversation through a closed round loop:

```python
# Simplified from AG2 internals
async def a_run_group_chat(pattern, messages, max_rounds):
    while rounds_remaining:
        next_speaker = await selector.select_speaker(chat_history)  # LLM call
        reply = await next_speaker.a_generate_reply(messages=chat_history)  # atomic
        chat_history.append(reply)
        rounds_remaining -= 1
    return event_stream
```

`a_generate_reply()` is **atomic** — it calls the LLM, processes the response, runs any tool
calls, and returns. There is no mechanism to pause mid-loop and inject an external workload
trigger before the next round begins. The loop runs to completion (`max_rounds` or a
termination condition) with no external interruptibility.

**What mozaiks needs:** "After this round, before the next agent runs, pause the entire
GroupChat, wait for external async work, then give me back control."

**AG2 current state:** No such primitive. The loop is opaque.

---

### 2.2 The Event Stream Is Read-Only Output — There Is No Input Channel

```python
# What a_run_group_chat actually returns
response: AsyncGenerator[BaseEvent, None] = await a_run_group_chat(
    pattern=pattern,
    messages=initial_messages,
    max_rounds=max_rounds,
)

async for event in response:          # ← READ ONLY. One direction.
    # TextMessageChunkEvent
    # TextMessageEvent
    # ToolCallEvent
    # InputRequestEvent
    # RunCompletion
    ...
```

The event stream is **strictly output**. There is no `response.send(data)`. There is no way
to inject information back into the running GroupChat after it has started. Once
`a_run_group_chat()` is called, AG2 runs to completion (or until you cancel the task).

`InputRequestEvent` is the one apparent exception — but see 2.4.

---

### 2.3 `ContextVariables` Are Scoped to a Single `GroupChat` Instance

```python
# Parent chat
parent_context = ContextVariables(data={"strategy": "build_api"})

# Child chat — entirely separate Python object
child_context = ContextVariables(data={"module": "auth_service"})

# These two objects NEVER communicate.
# AG2 has no mechanism to propagate context_variables between
# a_run_group_chat() calls or across GroupChat instances.
```

Fan-in requires the parent to know what each child produced. In AG2's current model,
`context_variables` from N child GroupChats are isolated Python objects that cease to
exist when those GroupChats complete. There is no aggregation surface.

**mozaiks workaround:** Serialize `context_variables` to MongoDB at the end of each child
run (via `AG2PersistenceManager`). On fan-in, read them back out manually.

---

### 2.4 The Only Pause Mechanism (`InputRequestEvent`) Is Single-Message Injection, Not Workload Orchestration

AG2 does have one externally-injectable pause: `InputRequestEvent`. When an agent emits
this event, the caller can inject a single message back into the conversation:

```python
async for event in response:
    if isinstance(event, InputRequestEvent):
        # AG2 is paused. We can inject one message.
        user_reply = await get_human_input()
        # ... inject user_reply back via the response mechanism
```

This is designed for **human-in-the-loop** — replace the next "human turn" in a GroupChat.
It is not designed for:
- Launching N parallel sub-workflows
- Aggregating N results
- Injecting structured context objects (not just a string message)
- Conditionally spawning different fan-out topologies based on prior agent output

Using `InputRequestEvent` for decomposition would require encoding entire workflow
coordination state as a fake "human message" — a workaround within a workaround.

---

### 2.5 Summary: What Decomposition Needs vs. AG2 Current State

| Requirement | AG2 Current State |
|---|---|
| Pause GroupChat mid-loop, externally | ❌ No primitive — requires `asyncio.Task.cancel()` |
| Inject structured context back into running GroupChat | ❌ No input channel (stream is read-only) |
| Share ContextVariables across GroupChat instances | ❌ Instance-scoped Python objects only |
| Fan-out: spawn N parallel GroupChats | ⚠️ Caller can do this manually, AG2 unaware |
| Fan-in: aggregate N results back to parent | ❌ No native surface — requires external storage |
| Resume a paused GroupChat with new context | ⚠️ `a_resume()` exists — works with full message replay, but no context injection |

---

## 3. Where mozaiks Takes Over

### 3.1 Three-Phase Execution Model

```
══════════════════════════════════════════════════════════════
  PHASE 1 — Parent Run (until decomposition trigger)
══════════════════════════════════════════════════════════════

[MOZAIKS] WorkflowPackCoordinator
        │  Listening on "structured_output_ready" event
        │
        ▼
[MOZAIKS] detect PatternSelection structured output with workflows[]
        │
        ├── [MOZAIKS] transport.pause_background_workflow(parent_chat_id)
        │     └── asyncio.Task.cancel()
        │         AG2's GroupChatManager task is cancelled.
        │         AG2 does not know why. State is already in MongoDB.
        │
        └── [MOZAIKS] asyncio.create_task(_run_workflow_background(child_N))
              per child — N tasks launched

══════════════════════════════════════════════════════════════
  PHASE 2 — N Parallel Child Runs (independent AG2 GroupChats)
══════════════════════════════════════════════════════════════

[AG2] a_run_group_chat() ← called independently per child
      AG2 sees an isolated, self-contained conversation.
      AG2 has no knowledge of parent or siblings.

[MOZAIKS] AG2PersistenceManager
      Serializes each child's final context_variables to MongoDB
      after each child completes.

[MOZAIKS] WorkflowPackCoordinator.handle_run_complete()
      Tracks completion of each child_chat_id.
      When all children done → triggers Phase 3.

══════════════════════════════════════════════════════════════
  PHASE 3 — Parent Resume (fan-in, aggregation, continuation)
══════════════════════════════════════════════════════════════

[MOZAIKS] Read child context_variables from MongoDB
          ← AG2PersistenceManager.fetch_chat_session_extra_context()
          Aggregated into {child_chat_id: extra_context} dict

[MOZAIKS] Write aggregated dict to parent session
          ← AG2PersistenceManager.patch_session_fields({"child_results": aggregated})

[AG2] group_manager.a_resume(messages=full_history_from_mongo)
      AG2 replays history → continues from resume_agent.
      AG2 sees one continuous conversation with the child results
      injected as context via the resumed message history.
```

### 3.2 Annotated Call Stack

```
User message → [MOZAIKS] simple_transport.handle_user_input_from_api()
                      │
                      ▼
              [MOZAIKS] orchestration_patterns.run_workflow_orchestration()
                      │
                      ├─ [MOZAIKS] AG2PersistenceManager — load history from MongoDB
                      ├─ [MOZAIKS] WorkflowManager — load YAML config
                      ├─ [MOZAIKS] patterns.create_ag2_pattern() → [AG2] AutoPattern.__init__()
                      ├─ [MOZAIKS] hooks.py → [AG2] register_hook("update_agent_state")
                      ├─ [MOZAIKS] handoffs.py → [AG2] OnCondition / AfterWork
                      ├─ [MOZAIKS] outputs.py → [AG2] response_format / StructuredOutputs
                      │
                      ▼
══════════════ AG2 EXECUTION BOUNDARY ════════════════════════
              [AG2] a_run_group_chat(pattern, messages, max_rounds)
                      │  GroupChatManager round loop runs
                      │  Hooks, handoffs, structured outputs fire
                      │  LLM calls made
                      │  BaseEvent objects emitted on async iterator
══════════════ MOZAIKS RESUMES ═══════════════════════════════
                      │
                      ▼
              [MOZAIKS] stream_and_process_events()
                      │  async for event in response:
                      │     ├─ Translate BaseEvent → WebSocket JSON
                      │     ├─ Detect structured_output_ready
                      │     │    └─ [MOZAIKS] dispatcher.emit("structured_output_ready")
                      │     │         └─ [MOZAIKS] WorkflowPackCoordinator.handle_structured_output_ready()
                      │     │               ├─ cancel parent asyncio.Task  ← Phase 1 end
                      │     │               └─ spawn N child asyncio.Tasks ← Phase 2 begin
                      │     ├─ Persist each turn to MongoDB
                      │     └─ Forward chunks to WebSocket
```

### 3.3 Key mozaiks Files

| What | File |
|---|---|
| Fan-out/fan-in coordinator | `mozaiksai/core/workflow/pack/workflow_pack_coordinator.py` |
| The one AG2 call (`a_run_group_chat`) | `mozaiksai/core/workflow/orchestration_patterns.py:665` |
| Resume call (`a_resume`) | `mozaiksai/core/workflow/orchestration_patterns.py:646` |
| Background task spawn + pause | `mozaiksai/core/transport/simple_transport.py` |
| Persistence (serialize ContextVariables) | `mozaiksai/core/data/persistence/persistence_manager.py` |
| Fan-out topology config | `workflows/<name>/_pack/workflow_graph.json` (`journeys` array) |
| AG2 pattern factory | `mozaiksai/core/workflow/execution/patterns.py` |

---

## 4. What the AG2 Streaming-Native Redesign Changes

The AG2 next-generation API introduces **bidirectional streaming**: a writable channel
into the running GroupChat alongside the existing read-only event output.

### 4.1 Current vs. Proposed API

**Current (read-only output)**
```python
response = await a_run_group_chat(pattern=pattern, messages=messages, max_rounds=n)
async for event in response:          # output only — no send()
    consume(event)
```

**Proposed (bidirectional stream)**
```python
async with pattern.stream() as stream:
    async for event in stream:
        if is_decomposition_trigger(event):
            # Fan out externally
            child_results = await asyncio.gather(*[run_child(c) for c in children])
            # Inject results BACK into the running GroupChat
            await stream.writer().send(aggregate(child_results))
            # Parent continues from here — no cancel, no a_resume needed
        else:
            consume(event)
```

### 4.2 How This Collapses the Three-Phase Model

| | Three-Phase (current mozaiks) | Bidirectional Stream (proposed) |
|---|---|---|
| Pause parent | `asyncio.Task.cancel()` | Not needed — stream pauses naturally while we `await gather()` |
| Run children | `N × asyncio.create_task()` | Same — `asyncio.gather()` |
| Aggregate | Read MongoDB after all children done | `aggregate(child_results)` in-memory |
| Resume parent | `a_resume(full_history_from_mongo)` | `stream.writer().send(data)` — parent continues natively |
| AG2 GroupChatManager | Sees 3 separate runs | Sees 1 continuous run |
| MongoDB round-trip | Required (only save/restore path) | Optional (can stay in-memory) |

The three-phase model exists entirely because there is no `stream.writer()`. With bidirectional
streaming, the entire `WorkflowPackCoordinator` phase management (pause → spawn N → wait →
resume) becomes a single `async with` block with an `await gather()` and a `.send()`.

### 4.3 What mozaiks Still Owns Even After the Upgrade

Even with native bidirectional streaming, the following remain mozaiks responsibilities
(they are not AG2 concerns):

- **N-child topology config** — reading `workflow_graph.json`, determining *which* workflows
  to spawn, what context to seed them with. This is domain/product logic, not framework logic.
- **Aggregation strategy** — `collect_all`, `merge_keys`, `first_wins`, etc. are business rules.
- **UI progress events** — `chat.workflow_batch_started`, per-child progress, completion tiles.
  AG2 has no UI layer.
- **MongoDB persistence** — durable sessions, multi-tenant isolation, audit log. Out of AG2 scope.
- **YAML declarative config** — agents, handoffs, tools, structured outputs. AG2 requires
  Python code; the YAML abstraction layer is a mozaiks product feature.
- **Child result routing** — deciding which child results go to which parent, fan-in contracts,
  schema validation. These are orchestration concerns above AG2.

---

## 5. Current State

| Capability | Status | Notes |
|---|---|---|
| `a_run_group_chat` — new chat | ✅ Production | Single call, event stream consumed |
| `a_resume` — paused/interrupted | ✅ Production | Full message history replayed from MongoDB |
| Declarative YAML workflow config | ✅ Production | 22-agent AgentGenerator live |
| `ContextVariables` seeding + hooks | ✅ Production | AG2-native, seeded from MongoDB per run |
| Handoffs (`OnCondition` / `AfterWork`) | ✅ Production | Expression + LLM eval types |
| Structured outputs (`response_format`) | ✅ Production | Per-agent |
| Fan-out (N parallel child GroupChats) | ✅ Production | `asyncio.create_task()` per child |
| Parent pause during fan-out | ✅ Production | `asyncio.Task.cancel()`, state in MongoDB |
| Parent resume after fan-in | ✅ Production | `a_resume()` with persisted history |
| Result aggregation (child → parent) | ✅ Production | `fetch_chat_session_extra_context()` per child → `patch_session_fields({"child_results": aggregated})` on parent, before `_resume_parent()` |
| Decomposition UI progress panel | 🔲 Partial | `chat.workflow_batch_started` event exists; per-child progress not built |
| Input/output contract validation | 🔲 Not built | Schema design in `MID_FLIGHT_JOURNEYS.md` |
| Multi-journey sequencing (`requires` field) | 🔲 Not built | Schema designed, coordinator work needed |

---

## 6. Three-Sentence Summary for the AG2 Team

> *"We use AG2's per-chat execution primitives exactly as designed — `a_run_group_chat`,
> `a_resume`, `ContextVariables`, hooks, handoffs, and structured outputs — without patching
> or forking anything in AG2.*
>
> *What we built on top is an orchestration layer that achieves mid-flight workflow decomposition
> by treating AG2 GroupChats as independently resumable units: we cancel the parent task,
> run N child GroupChats via `asyncio.create_task`, and call `a_resume` on the parent once
> children complete — AG2 sees three separate runs rather than one with internal branches.*
>
> *The bidirectional streaming API (`stream.writer().send()`) would collapse this three-phase
> workaround into a single continuous flow, eliminating the cancel/resume cycle and the
> MongoDB round-trip that exists solely to bridge between separate AG2 invocations."*

