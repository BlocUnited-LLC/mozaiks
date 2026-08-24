# Verification Priority

**Status: ACTIVE as of 2026-08-24.** This rule outranks feature work until it lifts.
See the lift condition at the bottom.

## The rule

Until one real end-to-end traversal proves the product loop (idea → generated
bundle → workbench → live preview → refinement → export), agent work must be
one of:

- **(a)** required to run the traversal,
- **(b)** converting something already built into something **proven** — a real
  run, a smoke test, a live seam check, or
- **(c)** fixing a defect that (a) or (b) actually surfaced.

**Do not add new framework capability surface.** Not a freeze on fixing — a
freeze on growing.

## Why

In the 48 hours to 2026-08-24, 40 PRs merged across `mozaiks` and `mozaiks-app`.
In the same window, the number of product-loop stages that moved from *built* to
**proven** was zero. Unproven surface is accumulating faster than it is being
verified, and defects are getting buried under newer work.

The concrete case that prompted this: `mozaiks gen` could not finish a run **by
design** — AgentGenerator opens by interviewing the user and the CLI cannot
reply (issue #383). That survived long enough for the fix meant to catch it
(#366) to be built against a directory the generator never writes to, and for
usage counters (#377) to be built on top of an unverified foundation. Three
layers of work on something nobody had watched run once. One live run found all
of it in six seconds — see issue #379 for the full trace.

## How to comply

- Before building, ask: **does this prove something, or add something?** If it
  adds, queue it and say so.
- Prefer running the real thing over adding a test that mocks it. A mocked test
  of an unproven seam proves the mock. The compile guard added in #380
  (`tests/test_workflow_context_authority_compile.py`) is the good pattern:
  it exercises all 14 real factory workflows, not stand-ins.
- **Verify a claimed defect exists before fixing it.** Same day as this rule, a
  suspected broken scaffold entry point (`ai.json` shipping
  `entry_point: "ValueEngine"` with no such workflow in the scaffold) was
  investigated and found *not* broken — `_is_runnable_workflow_name` and the
  Studio summary handle the empty-workspace case explicitly. The correct
  outcome was to drop the fix, not to ship one.
- When a real run teaches you something, write it to the tracking issue, not
  only into your session. Findings that live in a scratchpad do not survive.

## Corroboration from other lanes (2026-08-24)

Within hours of this rule being drafted, two unrelated lanes reported the same
pattern from different domains:

- **Refund pipeline**: its two highest-severity findings — a journal-duplicate
  race and a CI discovery gap — surfaced only when proofs ran against real
  infrastructure, *after* the code and the checks already looked green.
- **Seam audits**: an AST scan proving that every OSS symbol the hosted repo
  imports actually resolves caught three live `ImportError`s, one of which had
  silently disabled a paid-tier gate. Separately, a Playwright spec was found
  mocking the wrong 402 envelope shape — so the test *certifies* the bug it
  exists to catch.

Green CI is not proof. It is the absence of one kind of disproof.

## When this lifts

When the traversal has been observed end to end and the product-loop table in
`mozaiks-app/docs/operating-plan.md` §1 marks
`generate → workbench → preview → refine` as **proven**.
