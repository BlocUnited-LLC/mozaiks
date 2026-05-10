# Install Modes

Mozaiks currently has two supported ways to use the repo.

## Builder Mode

Use this if your goal is:

- open the Mozaiks Console
- describe the app you want
- build through the shared `factory_app` workflows

This is the normal-user path.

### Commands

```powershell
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
.\scripts\bootstrap-builder.ps1 -Workspace .\my-first-mozaiks-app
```

What happens:

- a workspace scaffold is created if needed
- `.venv` is created when missing
- the repo is installed in editable mode
- minimal config is written
- backend and frontend Console services start
- the browser opens at `/apps`

This is the Mozaiks path that is closest to an OpenClaw-style product flow.

## Framework Mode

Use this if your goal is:

- work on the runtime
- edit the CLI
- change platform hosts
- develop builder workflows
- debug app-bundle internals directly

This is the framework-developer path.

### Commands

```powershell
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Then use the lower-level commands directly:

- `mozaiks onboard --full`
- `mozaiks studio --dir <workspace> --open`
- `mozaiks init`
- `mozaiks serve`

This path is closer to an AG2-style local development workflow.

## Public Package Install

If a public `mozaiks` release is not yet available on PyPI, use the repo-first
builder path.

That means the supported first-time path today is still:

- clone the repo
- create a virtual environment
- run `pip install -e .`

After the first successful PyPI publish, this section should flip to:

```bash
pip install mozaiks
mozaiks quickstart --dir ./my-first-mozaiks-app
```

## Recommendation

If you are unsure, use builder mode:

```powershell
.\scripts\bootstrap-builder.ps1 -Workspace .\my-first-mozaiks-app
```
