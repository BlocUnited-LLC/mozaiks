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
- No authentication, no UI, no operations
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
- Still no auth, operations, or event bus

### 3. **integrated** - SaaS Builders
**For:** Teams building multi-user SaaS products
**Includes:** AI + chat + modules + event bus + auth
**Use when:** You need deterministic app actions, user management, and event-driven automation

```bash
mozaiks init integrated --name my-saas
```

**What you get:**
- Everything from chat
- Operations (deterministic logic)
- Event bus for workflow triggers
- Provider-neutral OIDC/JWT authentication

### 4. **full** - Product Builders
**For:** Production products with admin needs
**Includes:** Everything + admin portal + full management surfaces
**Use when:** You need complete product infrastructure

```bash
mozaiks init full --name my-product
```

**What you get:**
- Everything from integrated
- Admin portal with observability
- Full management surfaces
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
   - `app/app.json` with app identity and startup intent
   - `app/config/ai.json` + `app/config/shell.json`
   - `app/brand/theme_config.json` for visual identity
   - Empty `app/modules/`, `app/workflows/`, and `app/ui/pages/` stubs

4. **Explain starter content is opt-in:**
   - Default `init` creates shape only
   - Use `--starter` only when the user explicitly wants example workflow content

5. **Guide next steps:**
   - Run `mozaiks serve .` from the workspace root
   - Customize `app/app.json`, `app/config/ai.json`, and `app/brand/theme_config.json`
   - Add real workflows/operations only after the user has product context

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
- `app/app.json` (preset: chat)
- `app/config/ai.json` and `app/config/shell.json`
- `app/brand/theme_config.json`
- stub folders for `app/workflows/`, `app/modules/`, and `app/ui/pages/`

If you want starter example content too, use:
```bash
mozaiks init chat --name my-website-bot --starter
```

You can always add auth, operations, or admin features later with `mozaiks add`."

