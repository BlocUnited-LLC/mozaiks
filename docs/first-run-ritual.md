# First-Run Ritual

Mozaiks now has a guided bootstrap flow for first-time developers.

Use this when you clone the repo for the first time or want to reset local config cleanly.

---

## Why this exists

Many new developers are unsure which files are "real runtime state" vs "project configuration."

Use this model:

1. `app/app.json` + `.env` are your persistent project manifests.
2. Generated files (`infra/keycloak/realm-export.json`, Keycloak theme CSS) are derived artifacts.
3. Live runtime state lives in MongoDB + Keycloak volumes.

---

## Command flow

Run these commands from repo root:

```powershell
python -m mozaiksai.cli init --llm
python -m mozaiksai.cli doctor
python -m mozaiksai.cli up --frontend
```

---

## What `init` does

`python -m mozaiksai.cli init --llm` runs an LLM-guided one-question-at-a-time ritual.

`python -m mozaiksai.cli init` runs deterministic prompts without LLM.

1. app name
2. targets
3. whether auth is required
4. admin emails
5. optional OpenAI key

Then it:

1. writes/updates `app/app.json`
2. creates `.env` from `.env.example` if needed
3. updates key env values, including local dev auth defaults
4. regenerates Keycloak realm + theme artifacts

---

## What `doctor` checks

`python -m mozaiksai.cli doctor` validates:

1. required files exist and parse
2. `.env` critical keys are set
3. generated realm/theme artifacts are in sync with config
4. local health endpoints (`/api/health`, Keycloak ready endpoint)

It prints `[PASS]`, `[WARN]`, `[FAIL]` with direct fix commands.

---

## Non-interactive mode

For automation/CI/bootstrap scripts:

```powershell
python -m mozaiksai.cli init --non-interactive --app-name "My App" --app-id "my-app" --auth-enabled true
python -m mozaiksai.cli doctor --strict --skip-network
```

---

## One-command startup

After init/doctor:

```powershell
python -m mozaiksai.cli up --frontend
```

This runs generate + doctor preflight and starts the local docker stack. With `--frontend`, it also starts `npm run dev` inside `web_shell/`.
