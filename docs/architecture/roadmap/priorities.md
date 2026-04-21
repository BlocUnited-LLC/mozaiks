# Priorities

**Last updated:** 2026-03-19

Ordered work items derived from [gap-analysis.md](gap-analysis.md). Grouped into waves — complete each wave before moving to the next.

---

## Wave 1: Unblock the Funnel

*Goal: A stranger can try Mozaiks in 30 seconds and understand what it does.*

| # | Item | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 1.1 | Expose FastAPI auto-generated OpenAPI docs (`/docs`) | Not started | — | Near-zero effort. FastAPI does this already. Just make sure it's not disabled and publicly accessible. |
| 1.2 | Add `PLAYGROUND_MODE` env flag + rate limiter middleware | Not started | — | Per-IP session cap, token budget, model override to gpt-4o-mini |
| 1.3 | Create Fly.io deployment config (`fly.toml`) | Not started | 1.2 | Deploy existing Dockerfile with playground env vars |
| 1.4 | Deploy playground backend to Fly.io + MongoDB Atlas free tier | Not started | 1.3 | Skip Keycloak — use `chat` preset |
| 1.5 | Deploy frontend to Vercel or Cloudflare Pages | Not started | 1.4 | Vite app, standard static deploy |
| 1.6 | Playground landing page with embedded chat widget | Not started | 1.5 | Simple page: headline, widget, "clone for more" CTA |
| 1.7 | Session cleanup job (purge >24h playground data) | Not started | 1.4 | Cron or TTL index on MongoDB collection |
| 1.8 | Create `CHANGELOG.md` | Not started | — | Start from current version, backfill key milestones |

---

## Wave 2: Close the "Now What?" Gap

*Goal: After trying the playground, a user can build their own thing.*

| # | Item | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 2.1 | "Build Your First Workflow" tutorial | Not started | — | Simple single-agent example (not Backstage). Weather bot or FAQ bot. |
| 2.2 | "Create Your First Module" tutorial | Not started | — | Simple CRUD module with inline page. |
| 2.3 | AG2-to-Mozaiks migration guide | Not started | — | Side-by-side code comparison, porting checklist |
| 2.4 | API reference page (beyond OpenAPI) | Not started | 1.1 | Document WebSocket events, auth flows, key endpoints with examples |
| 2.5 | Common workflow patterns library | Not started | 2.1 | FAQ bot, data lookup, form collection, multi-step chain |

---

## Wave 3: Production Confidence

*Goal: Users trust Mozaiks enough to deploy to production.*

| # | Item | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 3.1 | GitHub Actions CI pipeline (pytest + lint) | Not started | — | Basic PR checks |
| 3.2 | E2E test suite | Not started | 3.1 | Boot server → API call → verify |
| 3.3 | Startup validator (check Docker, env vars, ports) | Not started | — | Run on `python run_server.py`, print clear error if something's wrong |
| 3.4 | Error troubleshooting page in docs | Not started | 3.3 | Common errors and fixes |
| 3.5 | Fly.io / VPS deployment tutorial (for user's own apps) | Not started | — | Beyond playground — how to deploy their project |
| 3.6 | SSL/TLS and domain configuration guide | Not started | 3.5 | Caddy or Let's Encrypt |

---

## Wave 4: Ecosystem Growth

*Goal: Mozaiks has a growing community and contributor base.*

| # | Item | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 4.1 | CLI: `mozaiks create workflow` and `mozaiks create module` | Not started | — | Scaffold from templates |
| 4.2 | CLI: `mozaiks dev` (start full dev stack) | Not started | — | Replace manual two-terminal flow |
| 4.3 | GitHub issue templates + "good first issue" labels | Not started | — | Lower barrier for contributors |
| 4.4 | Publish `@mozaiks/chat-ui` npm package | Not started | — | Enable React embedding without cloning |
| 4.5 | iframe / script-tag embed for non-React sites | Not started | 4.4 | Expand chat widget reach |
| 4.6 | Community channel (Discord or GitHub Discussions) | Not started | — | Support and feedback loop |

---

## Wave 5: Advanced App Surfaces

*Goal: Expand persistent app UI beyond the current bounded primitive set without reviving arbitrary raw page React generation.*

| # | Item | Status | Depends On | Notes |
|---|------|--------|------------|-------|
| 5.1 | Define primitive-extension workflow contract | Not started | — | Add the controlled "PrimitiveAgent" / extension lane with approval gates, registry updates, validator updates, tests, and docs requirements |
| 5.2 | Add opt-in extension gating via context/config flag | Not started | 5.1 | Default off. Primitive creation must be explicit and platform-owned |
| 5.3 | Establish primitive vs pattern vs subsystem decision rubric | Not started | 5.1 | Prevent misuse of primitives for canvases, editors, and builder runtimes |
| 5.4 | Ship first chart primitive family | Not started | 5.1, 5.2, 5.3 | Prefer reusable analytics/charting surfaces over ad hoc per-app React |
| 5.5 | Ship richer dashboard/widget primitives | Not started | 5.4 | Support deeper operational dashboards without breaking schema-first pages |
| 5.6 | Design rich editor / canvas / graph subsystem contracts | Not started | 5.3 | Whiteboards, workflow canvases, drag builders, and rich editors likely need subsystem contracts rather than simple primitives |

---

## Decision Log

Track key decisions made during roadmap execution.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-19 | Playground deploys on Fly.io + Atlas + Vercel | Cheapest path: ~$0 infra, pay only for LLM API usage |
| 2026-03-19 | Playground uses `chat` preset, skip Keycloak | Reduces infra cost and complexity for playground |
| 2026-03-19 | Rate limit by IP, not auth | No auth in playground — IP is the only identity signal |
| 2026-03-19 | gpt-4o-mini for playground | Cost control: ~$0.15/1M input tokens vs $2.50 for gpt-4o |
