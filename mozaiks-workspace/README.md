# mozaiks-workspace

This app was created with Mozaiks using the `chat` preset.

## Standalone Workspace Setup

Use this setup when this app workspace is being developed as its own repo.
The `.venv` belongs inside this workspace.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `MONGO_URI` before running Studio. Set `OPENAI_API_KEY` before running real workflows.

## Run Studio

```powershell
.\scripts\run-studio.ps1 -ForceStop
```

Equivalent package command:

```powershell
python -m mozaiks studio --dir . --open
```

## Two-Terminal Mode

```powershell
# Terminal 1
.\scripts\run-backend.ps1 -ForceStop

# Terminal 2
.\scripts\run-frontend.ps1 -ForceStop
```

The local scripts run this app against the installed `mozaiks` package. They do
not require a sibling checkout of the framework repository.

## Coding Agent Guidance

This workspace includes app-local guidance for coding agents:

- `AGENTS.md`
- `CLAUDE.md`
- `.claude/rules/`
- `.claude/skills/`

Those files describe this generated app boundary. They intentionally do not copy
the full Mozaiks framework repository rules.

Mozaiks refreshes managed guidance blocks automatically when workspace commands
run after an installed package upgrade. To inspect guidance status manually:

```powershell
mozaiks sync-agent-guidance --dir . --check
```

Manual repair options:

```powershell
mozaiks sync-agent-guidance --dir . --write-missing
mozaiks sync-agent-guidance --dir . --update
```

`--update` refreshes Mozaiks managed blocks only. Use `--force` only when you
explicitly want to overwrite app-local guidance files.
