# AGENTS.md

Coding-agent guidance for `{app_name}`.

This is a standalone Mozaiks app workspace created with the `{preset}` preset.
It consumes the published `mozaiks` framework package from `requirements.txt`.
Do not assume a sibling checkout of the Mozaiks framework repository exists.

## Standalone Workspace Setup

Use this setup when this app workspace is being developed as its own repo.
The `.venv` belongs inside this workspace.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `MONGO_URI` before running Studio. Set `OPENAI_API_KEY` before running real workflows.

## Run

```powershell
.\scripts\run-studio.ps1 -ForceStop
```

Two-terminal mode:

```powershell
.\scripts\run-backend.ps1 -ForceStop
.\scripts\run-frontend.ps1 -ForceStop
```

## Workspace Boundary

This repo owns app-specific behavior only:

- `app/app.json` - app identity and runtime flags
- `app/config/` - AI, shell, and app config
- `app/config/secrets.yaml` - names-only secret management contract; never stores raw values
- `app/brand/` - app branding assets and theme config
- `app/services/` - optional app-owned support code such as thin integrations, provider adapters, security helpers, and app-level routes
- `app/modules/` - deterministic app capabilities
- `app/data/contract.json` and `app/data/migrations/` - canonical app data contract and additive migration artifacts; generic helper code lives in `app/services/data/` when needed
- `workflows/` - app-local AI workflows
- `app/ui/` - app pages, route manifest, and custom UI registration
- `generated/` - staged generator output awaiting review/promotion
- `scripts/` - local launch wrappers around the installed `mozaiks` package

Framework/runtime changes belong in the upstream Mozaiks framework repository,
not in this app workspace.

## Development Rules

- Keep modules deterministic and contract-declared.
- Keep `backend/handler.py` thin; put business logic in `service.py` and data access in `repo.py`.
- Put app-owned external API clients in `app/services/integrations/`, provider-specific implementation boundaries in `app/services/adapters/`, provider-neutral auth/secret helpers in `app/services/security/`, and app-level routes in `app/services/routes/` only when needed. Common adapter areas include `auth/`, `source_control/`, `deployment/`, `dns/`, `registrar/`, `cloud/`, `storage/`, `secrets/`, and `payments/`.
- Do not put business actions, lifecycle state, emitted events, or persistence authority in app-level service support code; modules own those behaviors.
- Use `app/config/secrets.yaml` only as a names-only contract for secret provider/vault policy, env handles, and secret names. Never store raw API keys, tokens, passwords, connection strings, private keys, or webhook secrets in source.
- Prefer declarative page schemas before custom React.
- Mount custom React only through `app/ui/route_manifest.json` and `app/ui/index.js`.
- Keep shell/navigation changes in `app/config/shell.json`.
- Do not edit generated artifacts in place until they are intentionally promoted into `app/`.
- Update docs when setup, runtime behavior, module contracts, workflows, or UI surfaces change.

Scoped rules live in `.claude/rules/`. Claude-specific task skills live in
`.claude/skills/`.

The base guidance files are package-maintained by Mozaiks. Managed blocks are
refreshed automatically by workspace commands such as `mozaiks onboard`,
`mozaiks studio`, and `mozaiks serve` after the installed package changes. Use
`mozaiks sync-agent-guidance` only when you need to inspect or repair guidance
manually. App-specific rules or skills should be added only when this workspace
has concrete app behavior to document, such as a real module, workflow, page,
integration, or deployment surface.

