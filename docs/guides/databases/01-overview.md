# Database Overview

Mozaiks uses **two databases** — each owned by a different part of the stack.

---

!!! tip "New to Development?"

    **Let AI set up your databases!** Copy this prompt into Claude Code:

    ```
    I want to set up databases for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/databases/setup.md

    I'm using: [Docker / local installation / MongoDB Atlas]
    ```

---

## Why Two Databases?

| Database | Owned by | Purpose | You write queries? |
|----------|----------|---------|-------------------|
| **MongoDB** | Mozaiks runtime | Chat sessions, workflow state, artifacts | Yes |
| **PostgreSQL** | Keycloak | Users, credentials, sessions | No |

This is **polyglot persistence** — document data (chat, events) fits MongoDB naturally, while Keycloak requires relational integrity for identity management.

**Your app code never touches Postgres directly.** It's Keycloak's internal database.

---

## Quick Start (Docker)

One command starts everything:

```bash
docker compose -f infra/compose/docker-compose.yml up -d
```

| Service | Container | Port |
|---------|-----------|------|
| MongoDB 7 | `mozaiksai-mongo` | `27017` |
| PostgreSQL 16 | `mozaiksai-keycloak-db` | internal |
| Keycloak 26 | `mozaiksai-keycloak` | `8080` |

Verify:
```bash
docker compose -f infra/compose/docker-compose.yml ps
```

All services should show `healthy`. Keycloak takes ~30-60 seconds on first boot.

---

## Guide Sections

| Section | What You'll Learn |
|---------|-------------------|
| [MongoDB Setup](02-mongodb.md) | Local, Docker, or Atlas configuration |
| [Keycloak & PostgreSQL](03-keycloak.md) | Identity provider setup |
| [Production & Backup](04-production.md) | Production checklist, backup/restore |
