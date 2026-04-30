# Gap Analysis

**Last updated:** 2026-03-19

Full audit of the Mozaiks framework — what exists, what's missing, and how severe each gap is.

---

## Severity Key

| Level | Meaning |
|-------|---------|
| **Critical** | Blocks adoption or creates serious user drop-off |
| **High** | Slows adoption significantly; workarounds exist but are painful |
| **Medium** | Inconvenient but not a blocker; experienced devs can figure it out |
| **Low** | Nice-to-have; improves polish but not essential right now |

---

## 1. Live Playground / Dojo

**Severity: Critical**

The single biggest funnel gap. A user who discovers Mozaiks on GitHub or social media has no way to try it without cloning the repo, installing Docker + Python + Node, and configuring env vars. That's a 15-30 minute commitment before seeing anything.

### Exists

- `test-example.ps1` script for local quick-start
- Dockerfile and `docker-compose.prod.yml` ready for deployment
- Chat UI with embeddable widget component

### Missing

- [ ] Hosted playground at `dojo.mozaiks.ai` or similar
- [ ] `PLAYGROUND_MODE` env flag with rate limiting (per-IP session caps, token budget)
- [ ] Playground landing page with embedded chat widget
- [ ] Fly.io deployment config (`fly.toml`) or equivalent
- [ ] Session cleanup cron for playground data
- [ ] "Try Mozaiks" CTA that funnels to clone or managed platform after limit

### Why Critical

Every other gap compounds on this one. Without a playground, every user journey starts with friction. The funnel is: Awareness → Interest → **Evaluation** → Adoption. The playground IS the evaluation step.

---

## 2. API Documentation

**Severity: Critical**

### Exists

- `docs/reference/deep-dives/api-reference.md` — lightweight reference notes, ~5 endpoints, explicitly says "not exhaustive"
- 15+ route modules in `mozaikscore/core/routes/`: admin_observability, admin_users, analytics, app_config, app_metadata, events, health, modules, notifications, pages, profile, push_subscriptions, settings, status, subscriptions, subscription_sync, theme

### Missing

- [ ] Comprehensive REST API reference covering all route modules
- [ ] OpenAPI/Swagger auto-generated spec (FastAPI has this built in — just needs to be exposed)
- [ ] WebSocket event/message schema documentation
- [ ] Authentication flow documentation (Keycloak OIDC/PKCE)
- [ ] Postman collection or equivalent

### Why Critical

Backend developers (Journey 3: "headless AI API") and SaaS builders (Journey 4) cannot integrate without knowing the API contract. FastAPI generates OpenAPI for free — this is low-effort, high-impact.

---

## 3. "Build Your First Workflow" Tutorial

**Severity: High**

### Exists

- `docs/architecture/foundations/workflow-authoring-contracts.md` — formal contracts
- `docs/architecture/foundations/declarative-ag2-mapping.md` — YAML-to-AG2 mapping
- `.claude/skills/create-workflow/SKILL.md` — AI-assisted workflow creation
- Backstage example workflows (GreenRoom, WritersRoom, MainStage) as reference

### Missing

- [ ] Step-by-step beginner tutorial: "Build a weather chatbot in 10 minutes"
- [ ] Simple single-agent workflow example (not the complex 3-workflow Backstage chain)
- [ ] Common workflow patterns library (FAQ bot, data lookup, form collection, etc.)
- [ ] Troubleshooting guide for common workflow authoring mistakes

### Why This Matters

After running the example, every user asks "now what?" The activate step needs a guided path from example → custom workflow. The AI skill helps but only works in Claude Code/Copilot.

---

## 4. Module Authoring Guide

**Severity: High**

### Exists

- Canonical module contract docs in `docs/architecture/foundations/canonical-app-structure.md` and `docs/architecture/foundations/platform-authoring.md`
- Architecture reference in `docs/architecture/foundations/event-system.md`
- `mozaikscore/core/module_manager.py` — dynamic module loading

### Missing

- [ ] "Create Your First Module" tutorial
- [ ] module manifest-family documentation with all fields explained:
      `module.yaml`, `events.yaml`, `subscriptions.yaml`,
      `notifications.yaml`, `settings.yaml`, and `admin.yaml`
- [ ] Example modules beyond lineup_board (e.g. simple CRUD, webhook handler)
- [ ] Module ↔ workflow event wiring walkthrough
- [ ] Module testing patterns

### Why This Matters

The `integrated` and `full` tiers depend on modules. Any SaaS builder hits a wall when they need business logic beyond AI chat.

---

## 5. AG2/AutoGen Migration Guide

**Severity: High**

### Exists

- `docs/reference/deep-dives/ag2-touchpoints-and-extensions.md` — architecture-focused AG2 extension docs

### Missing

- [ ] "Migrating from AutoGen/AG2 to Mozaiks" guide
- [ ] Side-by-side code comparison (raw AG2 vs Mozaiks YAML)
- [ ] Agent porting checklist
- [ ] Known limitations / unsupported AG2 features
- [ ] "Why Mozaiks over raw AG2" positioning page

### Why This Matters

AG2 users are the highest-intent audience. They already understand multi-agent orchestration but are frustrated by the gap to production. A migration guide converts them fastest.

---

## 6. Deployment Guide (Beyond Docker Compose)

**Severity: Medium**

### Exists

- `infra/DEPLOYMENT.md` — Docker Compose, Kubernetes, GCP Cloud Run, Azure Container Apps
- `infra/docker/Dockerfile` — multi-stage production image
- `infra/compose/docker-compose.prod.yml` — full production stack

### Missing

- [ ] Fly.io deployment guide (cheapest path for playground and small apps)
- [ ] CI/CD pipeline examples (GitHub Actions, Azure DevOps)
- [ ] Database migration strategy for production upgrades
- [ ] SSL/TLS configuration guide
- [ ] Monitoring and alerting setup (Prometheus, Grafana, or cloud-native)
- [ ] Cost estimation guide for different deployment targets

### Notes

The existing deployment docs cover the major clouds. The Fly.io gap matters most for the playground initiative specifically.

---

## 7. Testing & CI

**Severity: Medium**

### Exists

- 26 test files covering orchestration, persistence, events, config, triggers, lifecycle
- Good coverage of runtime contracts and workflows
- `pytest` as test runner

### Missing

- [ ] E2E test suite (boot server → hit API → verify response)
- [ ] CI/CD pipeline config (GitHub Actions `pytest` step)
- [ ] Test coverage reporting
- [ ] Performance / load testing
- [ ] Frontend test setup (chat-ui component tests)

### Notes

Unit testing is solid. The gap is in automation (CI) and confidence at the integration boundary.

---

## 8. CLI Completeness

**Severity: Medium**

### Exists

- 3 commands: `mozaiks init`, `mozaiks add`, `mozaiks info`
- Tier preset system (engine, chat, integrated, full)
- LLM-guided initialization

### Missing

- [ ] `mozaiks create workflow <name>` — scaffold a new workflow from CLI
- [ ] `mozaiks create module <name>` — scaffold a new module from CLI
- [ ] `mozaiks dev` — start the development stack (backend + frontend + infra)
- [ ] `mozaiks deploy` — guided deployment
- [ ] CLI documentation page in docs/
- [ ] Shell completion support

### Notes

The CLI is functional for init. Workflow and module scaffolding from CLI would reduce dependency on AI code editors.

---

## 9. Error Handling & DX

**Severity: Medium**

### Exists

- Custom exceptions: `AuthError`, `DatabaseManagerError`, `ThemeValidationError`, `UIToolError`
- Logging infrastructure in `logs/`

### Missing

- [ ] User-friendly error messages (especially for common setup mistakes: missing env vars, Docker not running, wrong Python version)
- [ ] Error troubleshooting page in docs
- [ ] Startup validation that checks prerequisites and gives clear guidance
- [ ] Common error codes reference

### Notes

First-time setup is where most errors happen. A startup validator that checks Docker, env vars, and ports before attempting to boot would save hours of debugging.

---

## 10. CHANGELOG

**Severity: Medium**

### Exists

- Nothing. No CHANGELOG.md exists. README badge links to a nonexistent CHANGELOG.md.

### Missing

- [ ] `CHANGELOG.md` with version history
- [ ] Versioning strategy documented (semver?)
- [ ] Release process documented

### Notes

Open source contributors and users need to know what changed between versions. Important for trust.

---

## 11. Chat UI Embed SDK

**Severity: Low**

### Exists

- `GlobalChatWidgetWrapper` and `PersistentChatWidget` React exports
- `@mozaiks/chat-ui/core` portable entrypoint
- External auth adapter for embedding
- `useWidgetMode` hook

### Missing

- [ ] Published npm package
- [ ] iframe embed option for non-React sites
- [ ] Standalone HTML embed demo
- [ ] CDN-hosted script tag approach

### Notes

The React portability is solid. The npm publish and iframe/script-tag options expand reach to non-React developers.

---

## 12. Mobile Client

**Severity: Low**

### Exists

- React Native setup in `clients/mobile/`
- iOS + Android support
- Keycloak OIDC auth
- `.env.example` with documented variables

### Missing

- [ ] App Store / Play Store distribution guide
- [ ] Push notification setup
- [ ] Deep linking configuration
- [ ] Offline mode / sync strategy

### Notes

Mobile is well-structured. These are production deployment concerns, not framework gaps.

---

## 13. Contributing & Community

**Severity: Low**

### Exists

- `CONTRIBUTING.md` with ground rules, dev setup, PR expectations

### Missing

- [ ] "Good first issue" labels on GitHub
- [ ] Issue templates (bug report, feature request, workflow request)
- [ ] Community channels (Discord, GitHub Discussions)
- [ ] Contributor recognition / hall of fame
- [ ] Code of conduct

### Notes

Community infrastructure becomes important once there's traffic from the playground.

---

## Summary Matrix

| # | Area | Severity | Effort | Impact |
|---|------|----------|--------|--------|
| 1 | Live Playground / Dojo | Critical | Medium | Highest — unblocks the entire funnel |
| 2 | API Documentation | Critical | Low | High — FastAPI auto-generates OpenAPI |
| 3 | First Workflow Tutorial | High | Medium | High — closes the "now what?" gap |
| 4 | Module Authoring Guide | High | Medium | High — unblocks integrated/full tier users |
| 5 | AG2 Migration Guide | High | Medium | High — converts highest-intent audience |
| 6 | Deployment Guides | Medium | Medium | Medium — needed for production users |
| 7 | Testing & CI | Medium | Medium | Medium — needed for contributor confidence |
| 8 | CLI Completeness | Medium | Medium | Medium — reduces AI editor dependency |
| 9 | Error Handling & DX | Medium | Low | Medium — reduces support burden |
| 10 | CHANGELOG | Medium | Low | Medium — trust signal |
| 11 | Chat UI Embed SDK | Low | Medium | Low — expands reach |
| 12 | Mobile Production | Low | Medium | Low — production polish |
| 13 | Contributing & Community | Low | Low | Low — scales with traction |
