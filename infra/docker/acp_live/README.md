# Live repository ACP worker

Build the existing pinned offline images first, then build the live image from
the repository root:

```bash
docker build -f infra/docker/Dockerfile.preview -t mozaiks-sandbox:local .
docker build -f infra/docker/Dockerfile.acp-proof -t mozaiks-acp-proof:local .
docker build -f infra/docker/Dockerfile.acp-adapters -t mozaiks-acp-adapters:local .
docker build -f infra/docker/Dockerfile.acp-live-worker -t mozaiks-acp-live-worker:local .
docker image inspect --format '{{.Id}}' mozaiks-acp-live-worker:local
```

The trusted launcher pins the final image ID. Its stdin is a scoped repository
request with exact `create_paths` and `delete_paths` plus:

```json
{
  "live": {
    "adapter": "codex",
    "model": "gpt-5.3-codex",
    "gateway_url": "http://model-gateway:8765",
    "job_token": "one-time opaque token of at least 32 URL-safe characters",
    "max_wall_seconds": 90
  }
}
```

The launcher must supply no host mount or model credential to the worker,
mount fresh writable tmpfs at `/workspace`, `/tmp`, and `/home/sandbox`, and
restrict its network to the one-job model gateway. The gateway owns the real
provider key and accepts the synthetic token as `Authorization: Bearer`.
Codex routes `/v1/responses` through a private user-level `CODEX_HOME/config.toml`;
Claude Code routes `/v1/messages` through `ANTHROPIC_BASE_URL` and
`ANTHROPIC_AUTH_TOKEN`. The worker returns only a proposal and whole-workspace
archive. The trusted host must verify and stage that archive against the
approved snapshot before accepting any patch. This image is separate from the
offline fake-agent proof and is never selected by the offline Docker executor.
