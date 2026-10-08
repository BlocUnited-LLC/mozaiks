# Repository ACP model gateway

This is the credential boundary for one isolated Codex or Claude Code coding
turn. The Mozaiks refinement control plane still chooses the route, approved
files, budgets, validation, and promotion. AG2 ACP still drives the coding
agent. The gateway only carries model API calls from that agent to the selected
provider.

The trusted launcher starts this container without a published host port and
writes **one** newline-terminated JSON object to stdin:

```json
{"adapter":"codex","upstream_api_key":"<provider key>","job_token":"<random URL-safe token of at least 32 characters>"}
```

`adapter` is `codex` or `claude_code`. After binding to port `8765`, the
gateway writes exactly `READY\n` to stdout. It keeps serving when stdin
closes. The upstream key is never placed in the worker environment or passed
through an HTTP request. The worker receives only the job token, which it
sends as `Authorization: Bearer <token>` or `x-api-key: <token>`.

The image has no Python package dependencies beyond the digest-pinned base
image. The launcher should run it as UID `10001`, with a read-only root,
private network and no host port publication. The worker should have access
only to that private network and this gateway's alias `model-gateway:8765`;
the gateway needs separate egress for the fixed HTTPS model API host. Neither
the worker nor the gateway should receive a Docker socket or host filesystem
mount.

The fixed upstreams and routes are:

| Adapter | HTTPS upstream | Allowed POST paths |
| --- | --- | --- |
| `codex` | `api.openai.com` | `/v1/responses`, `/v1/chat/completions` |
| `claude_code` | `api.anthropic.com` | `/v1/messages`, `/v1/messages/count_tokens` |

These routes follow the [OpenAI Responses and Chat Completions API](https://developers.openai.com/api/reference/resources/responses/methods/create)
and [Claude Messages API](https://platform.claude.com/docs/en/api/overview).
The gateway ignores proxy environment variables, validates the upstream TLS
certificate, never follows a redirect, and forwards only selected headers.

Each gateway process permits at most 128 model requests, 8 MiB per request,
32 MiB per response, 4 simultaneous model requests, 8 connections, 10 minutes
per request, and 15 minutes total. It buffers JSON responses before returning
them, so an oversized response cannot look like a successful truncated JSON
message. Streaming SSE responses are forwarded immediately; if a stream
exceeds its byte or time budget, the connection closes. The coding provider
must treat an incomplete stream as a failed turn.

This transport budget does not cap model tokens or provider spend. A live
deployment also needs the trusted worker's own turn budget, provider-side
limits, and a compatibility smoke with the installed Codex or Claude Code
version before enabling ACP by default.
