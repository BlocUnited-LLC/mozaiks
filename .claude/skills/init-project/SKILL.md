---
name: init-project
description: Initialize a new Mozaiks project with tier selection. Guides through preset choice and project scaffolding.
argument-hint: "[optional: preset name like 'chat' or 'full']"
---

Help the user initialize a new Mozaiks project with the right tier preset.

## Four Development Tiers

Mozaiks uses a tier system to reduce complexity for developers who don't need the full stack:

### 1. **engine** - Headless AI API
**For:** Backend developers building AI APIs
**Includes:** AI workflow runtime only
**Use when:** You want pure REST API for workflow execution, no UI

```bash
mozaiks init engine --name my-api
```

**What you get:**
- AI workflow execution via REST API
- No authentication, no UI, no modules
- Minimal barrier to entry

### 2. **chat** - Chatbot Builders
**For:** Developers building chatbots and AI assistants
**Includes:** AI workflows + chat UI
**Use when:** You want a working chatbot with UI, but no complex backend

```bash
mozaiks init chat --name my-chatbot
```

**What you get:**
- Everything from engine
- Chat UI with workflow integration
- Still no auth, modules, or event bus

### 3. **integrated** - SaaS Builders
**For:** Teams building multi-user SaaS products
**Includes:** AI + chat + modules + event bus + auth
**Use when:** You need business logic, user management, and event-driven automation

```bash
mozaiks init integrated --name my-saas
```

**What you get:**
- Everything from chat
- Business modules (deterministic logic)
- Event bus for workflow triggers
- Keycloak authentication

### 4. **full** - Product Builders
**For:** Production products with admin needs
**Includes:** Everything + admin portal + subscriptions
**Use when:** You need complete product infrastructure

```bash
mozaiks init full --name my-product
```

**What you get:**
- Everything from integrated
- Admin portal with observability
- Subscription management
- Token usage tracking

## Workflow

When the user wants to create a new project:

1. **Ask what they're building:**
   - "I want a simple chatbot" → **chat**
   - "I want an AI API" → **engine**
   - "I'm building a SaaS product" → **integrated**
   - "I need everything including admin" → **full**

2. **Run the init command:**
   ```bash
   mozaiks init <preset> --name <app-name>
   ```

3. **Explain what was created:**
   - `platform/app.json` with preset
   - `platform/workflows/` with example workflow
   - `platform/pages/` with example page (if chat_ui enabled)
   - Directory structure matching the tier

4. **Guide next steps:**
   - Set up `.env` with OPENAI_API_KEY
   - Run backend: `python run_server.py`
   - If chat_ui: Run frontend: `cd app && npm run dev`

## Feature Upgrade Path

Users can always add more features later:

```bash
# Enable individual features
mozaiks add modules
mozaiks add event_bus
mozaiks add auth
mozaiks add admin

# Or upgrade to higher tier
mozaiks add --preset integrated
```

## When to Use This Skill

- User says "initialize new project"
- User asks "which tier should I use"
- User wants to create a chatbot, API, or app
- User is overwhelmed by full Mozaiks setup

## Example Interaction

**User:** "I want to build a simple chatbot for my website"

**You:** "For a simple chatbot, I recommend the **chat** tier. This gives you:
- AI workflows
- Chat UI
- No authentication complexity

Let me initialize your project:
```bash
mozaiks init chat --name my-website-bot
```

This will create:
- `platform/app.json` (preset: chat)
- Example workflow in `platform/workflows/HelloWorkflow/`
- Example page in `platform/pages/home/`

You can always add auth, modules, or admin features later with `mozaiks add`."
