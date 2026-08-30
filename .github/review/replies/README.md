# Canned replies

Template responses for common triage situations. Maintainers use them as
copy-paste starting points — personalize whenever it helps. Nothing here is
meant to be sent as-is if a real sentence would serve the contributor better.

Placeholders use `{{name}}` syntax. Common ones: `{{author}}` (GitHub login
without the `@`), `{{observations}}`, `{{reason}}`.

The HTML comment at the top of each file documents when to use it. It is not
part of the reply — delete it when you paste.

| File | Situation |
|---|---|
| `welcome-first-pr.md` | A first-time contributor opened a PR |
| `ai-slop.md` | A PR or issue shows signs of unverified AI-generated content |
| `needs-reproduction.md` | A bug report cannot be reproduced as written |

## Why this is deliberately short

Larger projects run a dozen or more of these, backed by a label taxonomy and
triage automation. Mozaiks does not have that volume yet, and a template
library nobody maintains is worse than no library. Add a new reply when you
have written roughly the same comment three times, not in anticipation.

## Tone

These are the first words a stranger reads from this project. Two rules:

1. **Say what happens next.** A contributor who knows their PR is queued
   behind a CI approval is waiting. One who does not is being ignored.
2. **Criticize the submission, never the person.** `ai-slop.md` in particular
   is about verification, not about whether someone used AI. The
   [AI policy](../../AI_POLICY.md) welcomes AI assistance and holds maintainer
   agents to the same standard — the reply should not read as if it forgot
   that.
