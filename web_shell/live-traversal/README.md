# Live traversal

Drives the real product loop end to end — idea to generated bundle — through a
running Studio, with real LLM calls and real Mongo. Nothing is mocked.

It exists because a mocked test of an unproven seam proves the mock. Every rule
in `traversal.spec.js` is there because a real run got it wrong first.

## Running it

It drives an **already-running** stack; it starts nothing itself.

```bash
# from web_shell/
npx playwright test --config=live-traversal/traversal.config.js
```

The Python interpreter for the evidence probe is resolved in order:
`MOZAIKS_TRAVERSAL_PYTHON`, a repo-local `.venv`, then `python3`/`python` on
PATH. Nothing is hardcoded, so this runs on any checkout.

Prerequisites, all of which have silently produced false results before:

1. **Studio running** on `:3000` (vite) and `:8000` (backend).
2. **A funded AI token wallet** for the identity the backend sees. The browser
   is unauthenticated, so that is usually `anonymous` — not the `dev-user` the
   frontend displays. They are different wallets.
3. **The stack must serve the code you are testing.** The dev stack loads OSS
   from the installed package, so your checkout edits are not necessarily what
   is running. Check before believing any result.

## Reading the result

**Only one outcome passes.** Every other verdict throws, so the process exit code
and the verdict agree. Earlier versions logged a failure and exited `0`; four
consecutive failed traversals were recorded as four passing tests.

| Verdict | Passes? | Meaning |
|---|---|---|
| `VALIDATED ARTIFACT - <id> (<status>, n files)` | yes | an artifact from this run's chats reached `lifecycle_status: current` **and** carries a validation result |
| `NOT VALIDATED - ...` | no | an accepted artifact exists but nothing validated it |
| `NO ACCEPTED ARTIFACT - ...` | no | files were written; none promoted to the accepted version |
| `NO BUNDLE - none of our N chat(s) produced files` | no | ran, did not fail, produced nothing |
| `WORKFLOW FAILED in <stage>` | no | a workflow died; the backend log has the reason |
| `BLOCKED - <reason>` | no | never ran — e.g. the token gate rejected it |
| `UNKNOWN - the evidence probe failed` | no | the probe could not answer; an unmeasured run is not a passing run |

`artifacts/verdict.json` holds the structured outcome, provenance, chat ids,
workflow sequence, and the artifact rows behind the verdict.

### A caveat about validation

`app_validation_status` on `ArtifactVersions` is populated only when an app
validation actually runs. At the time of writing it is absent on effectively
every record in a development database, and the one exception reads `skipped`.
So `VALIDATED ARTIFACT` is currently **unreachable in practice** — that is the
honest state of the loop, not a bug in the harness. `NOT VALIDATED` is the
verdict to expect from an otherwise complete build, and it distinguishes "the
build finished but nothing checked it" from "the build did not finish".

## Diagnostics it emits

- `PROVENANCE` — OSS SHA and branch, and the installed package path and version
  the probe's interpreter actually imports, so a result can be tied to the code
  that produced it
- `CHAT` — every chat id this run drove; bundles and artifacts are attributed to these only
- `GATE` — a gate it clicked, by `data-testid` handle or by label
- `GATE_INERT` / `GATE_FORGIVEN` — a gate that changed nothing twice is skipped,
  then retried rather than retired: a healthy gate once looked inert and the run
  replied into the composer for twenty minutes with the button on screen
- `REPLY_LOOP` + `UNMATCHED_BUTTON` — replying without advancing, plus every
  visible button, so an unrecognised gate names itself instead of being guessed
- `WORKFLOW_FAILED` — stops immediately instead of waiting out the stall timer

## Things that have fooled this harness

Each cost a run, and each is now guarded:

- **Counting bundles globally.** Other agents build against the same Mongo. A
  concurrent bundle raised the count and the harness called it success — for an
  app it had not built. Attribution is by `chat_id` now.
- **Watching `generated/apps`.** Generated code lives in Mongo. That directory
  stays empty through a successful build.
- **Measuring `body.innerText`.** Artifact panels are outside body text, so a
  live screen reads as a stall. It measures serialized DOM plus button count.
- **Anchored gate labels.** Option cards concatenate badge + title, so the
  accessible name can start with `FASTEST`, not the title. Patterns are
  unanchored.
- **Treating a reply as progress.** A dialog that wants a click looks identical
  to a healthy interview from the DOM.
