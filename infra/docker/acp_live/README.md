# Live repository ACP worker

## Build the images

Build every image from the repository root of the exact commit you will run,
in this order, on a Linux Docker engine:

```bash
docker build -f infra/docker/Dockerfile.preview -t mozaiks-sandbox:local .
docker build -f infra/docker/Dockerfile.acp-proof -t mozaiks-acp-proof:local .
docker build -f infra/docker/Dockerfile.acp-adapters -t mozaiks-acp-adapters:local .
docker build -f infra/docker/Dockerfile.acp-live-worker -t mozaiks-acp-live-worker:local .
docker build -f infra/docker/Dockerfile.acp-model-gateway -t mozaiks-acp-model-gateway:local .
docker image inspect --format '{{.Id}}' mozaiks-acp-live-worker:local
docker image inspect --format '{{.Id}}' mozaiks-acp-model-gateway:local
```

The proof and live worker Dockerfiles take `BASE_IMAGE`, `PROOF_IMAGE`, and
`ADAPTER_IMAGE` build arguments when you use other tags. The trusted launcher
pins the final image IDs, never the tags. See
[the gateway README](../acp_gateway/README.md) for the gateway image.

**Build once, then reuse the same image IDs.** These builds are not
bit-reproducible: `Dockerfile.preview` starts from an unpinned base image, and
an image ID includes build timestamps, so a rebuild of the same commit yields
different IDs. Build the images once, record the two IDs printed above, and run
the opt-in gates 1-3 and the operator transport smoke against those same local
image IDs. Do not rebuild between the gates and the smoke; if you must rebuild,
run the gates again on the new IDs.

## Worker contract

Its stdin is a scoped repository request with exact `create_paths` and
`delete_paths` plus:

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

The worker constructs the typed `ControlPlaneACPProviderConfig` directly from
this approved turn envelope. The app's checkpoint `llm_profiles` do not select
the ACP adapter or its model.

The launcher must supply no host mount or model credential to the worker,
mount fresh writable tmpfs at `/workspace`, `/tmp`, and `/home/sandbox`, and
restrict its network to the one-job model gateway. The gateway owns the real
provider key and accepts the synthetic token as `Authorization: Bearer`.
Codex routes `/v1/responses` through a private user-level `CODEX_HOME/config.toml`;
Claude Code routes `/v1/messages` through `ANTHROPIC_BASE_URL` and
`ANTHROPIC_AUTH_TOKEN`. The worker returns only a proposal and whole-workspace
archive. The trusted host must verify and stage that archive against the
approved snapshot before accepting any patch. The host also rejects a result
that copies the job token into returned source or proposal text. This image is
separate from the offline fake-agent proof and is never selected by the offline
Docker executor.

## Opt-in Docker gates (no model, no key)

`tests/test_repository_live_docker_gates.py` runs the real transport with
images derived from the two pinned images: a scripted worker
([`acp_live_gates/gate_worker.py`](../acp_live_gates/gate_worker.py)) and the
real gateway code in front of an in-container fake upstream
([`acp_live_gates/fake_gateway.py`](../acp_live_gates/fake_gateway.py)). The
upstream credential is a random canary. The gates check that the worker cannot
reach the internet, the Docker host, the metadata address, or the gateway's
egress bridge; that the canary appears in no container or network inspection,
process argument, worker environment, output, or archive; and that update,
exact create and delete, read-only or out-of-scope edits, malformed output,
copied job tokens, crashes, and timeouts fail closed with every container and
network removed. They are skipped unless explicitly enabled and are not part of
CI:

```bash
WORKER_ID="$(docker image inspect --format '{{.Id}}' mozaiks-acp-live-worker:local)"
GATEWAY_ID="$(docker image inspect --format '{{.Id}}' mozaiks-acp-model-gateway:local)"
{
  echo "oss_sha $(git rev-parse HEAD)"
  echo "worker_image_id $WORKER_ID"
  echo "gateway_image_id $GATEWAY_ID"
  MOZAIKS_RUN_REPOSITORY_LIVE_GATES=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH="$PWD" \
  MOZAIKS_REPOSITORY_LIVE_WORKER_IMAGE_ID="$WORKER_ID" \
  MOZAIKS_REPOSITORY_LIVE_GATEWAY_IMAGE_ID="$GATEWAY_ID" \
  python -m pytest -q --no-cov --reruns 0 -s tests/test_repository_live_docker_gates.py
} 2>&1 | tee <evidence directory outside the checkout>/gates-run.log
```

Keep `gates-run.log`: it is the gates run record that the smoke receipt links
to. Afterwards
`docker ps -a --filter label=mozaiks.refinement.repository_turn.worker=live`
and `docker network ls --filter name=mozaiks-acp-` must both be empty.

## Operator transport smoke (one real turn)

`scripts/smoke_repository_live_acp_turn.py` runs exactly one real Codex or
Claude Code turn against a local throwaway snapshot. It is for an operator at a
terminal, under an accepted ADR 0015 decision; it never runs in CI or under
pytest, and it writes nothing to GitHub, a database, or the snapshot. It
refuses unless `MOZAIKS_RUN_REPOSITORY_LIVE_ACP=1` is set, the terminal is
interactive, the imported `mozaiksai` resolves into the running checkout, that
checkout has no uncommitted tracked changes, and it has no untracked,
non-ignored file where imported code lives (under `mozaiksai/`, `scripts/`, any
other checkout package this process imported, or an importable top-level
module). It also refuses a `--snapshot-dir` inside this checkout or inside any
git working checkout (a parent directory holding `.git`).

1. Create a throwaway snapshot outside any git working checkout, for example
   with `git archive <commit> | tar -x -C <empty directory>`, and note the
   40-hex commit.
2. Create a dedicated provider key with a verified hard spend limit. Claude
   Code needs an Anthropic API key; Codex needs an OpenAI API key. Do not use
   an OAuth or subscription login: the gateway sends the key only as an API
   key, and the worker refuses login credentials in its environment.
3. Run the smoke from this checkout:

```bash
MOZAIKS_RUN_REPOSITORY_LIVE_ACP=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH="$PWD" \
python scripts/smoke_repository_live_acp_turn.py \
  --snapshot-dir <throwaway snapshot> \
  --baseline-sha <40-hex commit of the snapshot> \
  --edit README.md \
  --read-only docs/overview.md \
  --instruction "Add one sentence to the README introduction." \
  --adapter claude_code \
  --model <exact model id> \
  --worker-image-id "$(docker image inspect --format '{{.Id}}' mozaiks-acp-live-worker:local)" \
  --gateway-image-id "$(docker image inspect --format '{{.Id}}' mozaiks-acp-model-gateway:local)" \
  --max-wall-seconds 300 \
  --gates-receipt <evidence directory>/gates-run.log \
  --output-dir <new empty directory outside the snapshot and checkout>
```

`--edit` names the one file the agent may change; `--read-only` may be given
at most twice. `--max-wall-seconds` must be between 90 and 600. Paste the key
once at the hidden prompt; it is never read from the environment, arguments,
or files, and only the gateway container receives it, over stdin.

`--gates-receipt` is optional but expected for the ADR 0015 smoke. Pass the
saved gates run record; the smoke refuses a record that does not name both
`--worker-image-id` and `--gateway-image-id`, and the receipt carries its
SHA-256 digest. If the record is kept elsewhere, pass its `sha256:<64 hex>`
digest instead; the receipt then marks the link as operator-supplied
(`"source": "digest"`).

The output directory receives `receipt.json` and, after the host stages and
finalizes the returned archive, `host-verified.diff`. The receipt holds no
source or key: the OSS commit, image IDs, adapter, model, approved path,
proposal status, `repository_patch_digest`, advisory usage (or `null`), an
error code when the turn failed closed, the gates record digest
(`gates_receipt`), and `cleanup`. `cleanup.containers_removed` and
`cleanup.networks_removed` report what the executor's cleanup path confirmed:
`true` when it removed (or proved absent) every container and network the turn
created, `false` when it reported a removal failure, and `null` when it could
not confirm. Error codes are limited to the `REPOSITORY_*` families; any other
failure is recorded as its exception type only.

A person must read the diff. Then revoke the key, compare the receipt usage
with the provider console, and check that no `mozaiks-acp-*` container or
network remains. Post the receipt, the key revocation, and the gates record
digest on tracking issue
[#956](https://github.com/BlocUnited-LLC/mozaiks/issues/956).
