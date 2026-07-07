<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->
# CLAUDE.md

This file guides Claude Code when working in `MozaiksFactory`.

Read `AGENTS.md` first. This is a generated Mozaiks app workspace using the
`chat` preset, not the Mozaiks framework source repository.

## Core Principle

Keep app logic inside the canonical app workspace:

```text
app/
  app.json
  config/
  security/
  brand/
  services/  # optional integrations/adapters/routes support code
  modules/
  ui/
workflows/
```

Use the installed `mozaiks` package for runtime, CLI, Studio, factory bundle,
and web shell behavior.

## Where To Put Work

| Work | Location |
|------|----------|
| App identity/config | `app/app.json`, `app/config/` |
| Shell, navigation, footer, mobile chrome | `app/config/shell.json` |
| Secret management contract, names only | `app/security/secrets.yaml` |
| Branding/theme assets | `app/brand/` |
| App-owned external clients | `app/services/integrations/<service>_client.py` |
| App-owned provider adapters | `app/services/adapters/<area>/<provider>.py` |
| App-specific auth provider mechanics | `app/services/adapters/auth/<provider>.py` |
| Provider-neutral deployment artifacts | bundle-root `Dockerfile`, `docker-compose.yml`, `env.example`, `deployment.manifest.json`, optional `.github/workflows/deploy.yml` |
| App-level routes, only when needed | `app/services/routes/` |
| Data contract and migration artifacts, only when `data/contract.json` is present | `app/data/contract.json`, `app/data/migrations/` |
| Deterministic app capabilities | `app/modules/<module_id>/` |
| AI workflow behavior | `workflows/<WorkflowName>/` |
| Declarative pages | `app/ui/pages/` |
| Custom React pages/components | `app/ui/pages/custom/`, `app/ui/components/`, `app/ui/index.js` |
| Staged generated output | `generated/` |
| Local process wrappers | `scripts/` |

## Do Not

- Do not vendor or edit Mozaiks framework internals in this app repo.
- Do not hardcode workflow names inside module business logic.
- Do not bypass module contracts with undeclared routes or side channels.
- Do not put business logic directly in `backend/handler.py`.
- Do not turn provider adapters into modules or put module business state in `app/services/`.
- Do not use `app/services/` for provider-neutral deployment artifacts; generated `Dockerfile`, `docker-compose.yml`, `env.example`, `.github/workflows/deploy.yml`, and `deployment.manifest.json` belong at the app bundle root.
- Do not copy hosted platform provider adapters into this app; consume hosted deployment, DNS/domain, billing, wallet, and platform operations through hosted API clients/facade modules and host-owned records.
- Do not copy framework runtime auth into the app; generic auth belongs in the installed `mozaiks` package.
- Do not put raw secret values in `app/security/secrets.yaml`; it is a names-only contract.
- Do not use custom React when a declarative page schema is sufficient.
- Do not mutate generated artifacts without review/promotion.

## Validation

For non-trivial changes, run the narrowest practical checks:

```powershell
.\scripts\run-studio.ps1 -DryRun
.\scripts\run-backend.ps1 -DryRun
.\scripts\run-frontend.ps1 -DryRun
```

Then run the app or targeted tests relevant to the touched area.

## Rules And Skills

Use `.claude/rules/` for path-scoped guidance and `.claude/skills/` for common
tasks such as modules, pages, workflows, setup, and docs maintenance.

These base files are maintained by the installed `mozaiks` package. Add
app-specific rules or skills only when generated or hand-authored app behavior
needs narrower instructions. Studio/factory-generated modules and workflows
should own those app-specific additions when they become concrete.
<!-- END MOZAIKS MANAGED: agent-guidance -->
