# web_shell

`web_shell/` is the repo-local Vite shell used to develop and preview Mozaiks
UI surfaces. It is not a generated app workspace, and contributors do not need
any private hosted-product repo to run it.

## What It Loads

- By default, the shell resolves the first-party builder/reference app bundle at
  `factory_app/app`.
- If `PLATFORM_PATH` or `MOZAIKS_APP_WORKSPACE_PATH` is set, the shell resolves
  that selected app bundle or workspace instead.
- Shared build sequencing still comes from
  `factory_app/workflows/extended_orchestration/extension_registry.json` when no
  app-local workflow root overrides it.

## Quick Start

From the repo root:

```powershell
npm --prefix web_shell install
.\scripts\run-studio.ps1
```

That starts:

- the Studio backend on `http://localhost:8000`
- the Vite frontend on `http://localhost:3000/apps`

## Other Dev Modes

Frontend only:

```powershell
npm --prefix web_shell install
.\scripts\run-frontend.ps1
```

Split backend/frontend terminals:

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

Direct Vite command:

```powershell
npm --prefix web_shell run dev -- --host 0.0.0.0 --port 3000 --strictPort
```

## Path Selection

- Leave `PLATFORM_PATH` unset to use `factory_app/app`.
- Pass `-AppWorkspacePath <path>` to `scripts/run-backend.ps1` or
  `scripts/run-frontend.ps1` to point at an external app workspace.
- `PLATFORM_PATH` may target either an app bundle directory containing
  `app.json` or a workspace root containing `app/app.json`.

## What You Usually Edit

| Path | Role |
|------|------|
| `factory_app/app/` | First-party builder/reference app bundle loaded by default |
| `factory_app/app/ui/` | First-party UI pages, custom React, and route ownership |
| `factory_app/app/config/` | Shell, AI, auth, and other app-level config |
| `factory_app/app/brand/` | Brand assets and theme config |
| `factory_app/workflows/` | Shared builder workflows and extension registry |
| `web_shell/vite.config.js` | Shell path resolution and Vite/runtime integration |

`web_shell/` hosts those surfaces; it is not the canonical place to author app
contracts or builder workflows.

