# Paired Projects/Tasks executable acceptance

The fixture supplies **scripted AG2 worker responses** for one synthetic Projects/Tasks contract. The real AppGenerator task batch admission, deterministic materializer, generated-bundle validator, loader, and platform host process consume those responses. No live LLM call occurs here, and this fixture does not prove unattended app design quality.

Presentation A uses schema pages for task search, create, details, editing and status. Presentation B uses a custom JSX planning board and inspector, plus the same declarative Projects page. Both bundles have byte-identical generated `data/` and `modules/` files. B's `ui/route_manifest.json` declares only `/board`; the platform discovers `/projects` from `ui/pages/projects.yaml`.

From a Python environment with the repository's pinned requirements installed and Docker Desktop running:

```powershell
./scripts/run_paired_experience_demo.ps1 -Python 'C:\path\to\python.exe'
```

The script checks the AG2 version against `requirements.txt`, starts a disposable local `mongo:7` container, runs `tests/test_paired_experience_demo_960.py`, then stops that exact container. It writes generated A/B bundles, per-file SHA-256 values, task and runtime version receipts, and a host/API receipt under `.local/evidence/paired-experience-960/` (ignored by Git). The host test uses separate local platform processes, a local JWKS and signed tokens, and a unique database per run. It seeds one project and twelve tasks in A, verifies page 2 and search, then reads and updates those same records in B. It also checks missing auth and cross-user read, update, and owner-forgery denial.

Without Docker, the scripted admission, assembly, static validation, loader, and same-contract checks run with:

```powershell
python -m pytest -q --no-cov tests/test_paired_experience_demo_960.py
```

The host test skips unless `MOZAIKS_PAIRED_DEMO_MONGO_URI` points to a local disposable MongoDB. The receipts are synthetic local test data; no production or staging credentials are used.

This first slice does **not** prove browser rendering, keyboard/pointer interaction, visual quality, mobile refinement, E2B, or live LLM generation. Those require the follow-up browser/refinement acceptance against these admitted bundles.
