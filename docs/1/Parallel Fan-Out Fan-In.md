# Parallel Fan-Out / Fan-In — A Production Use Case for the AG2 Streaming Redesign

---

## 1. The Problem

Consider a user request like: *"Build me a SaaS app with an auth module, a billing
module, and an API gateway."*

A single GroupChat can't handle this well. The context window bloats with three unrelated
domains, agent specialization breaks down, and the work is inherently parallelizable —
there's no reason the billing module has to wait for the auth module to finish.

The natural answer is to split the request into N independent GroupChats (one per module)
that run **concurrently**, then merge their results back into the original conversation.

What we need is for a running GroupChat to fork into N parallel child GroupChats at runtime,
wait for all of them, collect their outputs, and resume where it left off.

The system must:

1. **Pause** the running parent GroupChat
2. **Fan out** N independent child GroupChats in parallel
3. **Wait** for all N to complete
4. **Aggregate** the results from each child
5. **Resume** the parent GroupChat with the aggregated context

---

## 2. Why AG2 Can't Do This Today

Three things block this:

**The event stream is read-only.** `a_run_group_chat()` returns an async iterator of
`BaseEvent` objects — output only. There is no `stream.send()` to inject results back
into a running GroupChat. Once launched, AG2 runs to completion or you cancel the task.

**`ContextVariables` are scoped to one GroupChat.** Parent and child GroupChats have
separate `ContextVariables` Python objects that never communicate. There's no built-in
way for child results to flow back to the parent.

**`InputRequestEvent` is for human messages, not orchestration.** It lets you inject a
single string message back into a conversation. It's not designed for launching N parallel
GroupChats and aggregating structured results.

---

## 3. What Mozaiks Built (The Workaround)

We treat each AG2 GroupChat as a stateless, resumable unit and coordinate externally:

```
Phase 1 — Parent runs until a planning agent emits "split into N child GroupChats"
           → We cancel the parent's asyncio.Task (AG2 stops, state is in MongoDB)

Phase 2 — N child GroupChats run in parallel via asyncio.create_task()
           → AG2 sees N independent conversations (no knowledge of parent or siblings)
           → Each child's context_variables are serialized to MongoDB on completion

Phase 3 — All children done → read child results from MongoDB
           → Call a_resume() on the parent with full history
           → AG2 replays and continues as one conversation
```

This works. It's in production. But it exists entirely because there's no write channel
back into a running GroupChat.

---

## 4. How the Proposed Streaming API Solves This

The bidirectional streaming API from the redesign proposal eliminates the workaround:

```python
# TODAY — three separate AG2 invocations, MongoDB round-trips to bridge them
response = await a_run_group_chat(pattern, messages, max_rounds)  # Phase 1
# ... cancel task, run N children, read MongoDB, call a_resume()  # Phase 2-3

# WITH BIDIRECTIONAL STREAMING — one continuous flow
async with agent.stream() as stream:
    async for event in stream.listen():
        if is_decomposition_trigger(event):
            # Fan out — same as today
            child_results = await asyncio.gather(*[run_child(c) for c in children])
            # Fan in — THIS IS THE MISSING PIECE
            await stream.writer().send(aggregate(child_results))
            # Parent continues natively. No cancel. No a_resume. No MongoDB round-trip.
```

`stream.writer().send()` is the primitive that makes fan-out/fan-in native. Without it,
we need three phases. With it, we need one `async with` block.

A key consequence: today we **require** MongoDB as a state bridge between phases — child
`ContextVariables` are serialized to MongoDB because the Python objects die when tasks are
cancelled, and `a_resume()` replays full history from MongoDB because the parent GroupChat
was killed. With bidirectional streaming, the parent never stops — its `ContextVariables`
stay alive in memory, child results return from `asyncio.gather()` as Python objects, and
`.send()` injects them directly. MongoDB is still used for session recording, audit, and
crash recovery, but it stops being a **required intermediary** just to pass context between
parent and children.

---

## Summary

We have a production system doing parallel fan-out/fan-in on top of AG2 today. The
workaround — cancel parent, run N children, resume parent from MongoDB — works but exists
solely because the current event stream is output-only.

The `stream.writer().send()` in the proposed streaming redesign is the exact primitive
that would collapse this three-phase workaround into a single continuous flow. This is a
concrete, production-validated use case for bidirectional streaming in AG2.