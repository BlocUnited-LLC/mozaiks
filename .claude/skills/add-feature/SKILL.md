---
name: add-feature
description: Add features to an existing Mozaiks project. Helps users upgrade tiers or enable individual capabilities.
argument-hint: "[optional: feature name or preset]"
---

Help the user add features to an existing Mozaiks project.

## Available Features

Mozaiks features are additive. You can enable them individually or upgrade to a higher tier preset.

### Individual Features

| Feature | What It Enables | Requires |
|---------|----------------|----------|
| **chat_ui** | Frontend chat interface | - |
| **modules** | Business logic modules and module routes | - |
| **event_bus** | Domain event bus for module↔workflow communication | - |
| **auth** | JWT validation, Keycloak integration | Docker services |
| **admin** | Admin portal with observability, token tracking | auth |

### Tier Presets

| Preset | Features Included |
|--------|-------------------|
| **engine** | ai_runtime |
| **chat** | ai_runtime, chat_ui |
| **integrated** | ai_runtime, chat_ui, modules, event_bus, auth |
| **full** | All features |

## Commands

### Enable Individual Feature
```bash
mozaiks add <feature>
```

Examples:
```bash
mozaiks add modules       # Add business logic capability
mozaiks add event_bus     # Add event-driven automation
mozaiks add auth          # Add Keycloak authentication
mozaiks add admin         # Add admin portal
```

### Upgrade to Higher Tier
```bash
mozaiks add --preset <tier>
```

Examples:
```bash
mozaiks add --preset chat        # Upgrade to chat tier
mozaiks add --preset integrated  # Upgrade to integrated tier
mozaiks add --preset full        # Upgrade to full tier
```

## What Happens

When you add a feature:

1. **Updates platform/app.json:**
   - Adds feature override OR upgrades preset
   - Example: `{"features": {"modules": true}}`

2. **Shows next steps:**
   - What directories to create
   - What config to update
   - What services to start

3. **Requires restart:**
   - Backend reads app.json at startup
   - Restart to apply changes

## Feature-Specific Next Steps

### Adding **modules**
```
Next steps:
1. Create platform/modules/<name>/
2. Add module.json with metadata
3. Add handler.py with FastAPI routes
4. Restart backend
```

### Adding **event_bus**
```
Next steps:
1. Use event_bus.publish() to emit app events
2. Add workflow triggers in orchestrator.yaml
3. Restart backend
```

### Adding **auth**
```
Next steps:
1. Start Docker services: docker compose -f infra/compose/docker-compose.yml up -d
2. Configure .env: KEYCLOAK_* variables
3. Update platform/app.json: authRequired: true
4. Restart backend
```

### Adding **admin**
```
Next steps:
1. Configure admins in platform/app.json
2. Access admin portal at /admin
3. Requires auth to be enabled
```

### Adding **chat_ui**
```
Next steps:
1. Start frontend: cd app && npm run dev
2. Configure branding in platform/brand/
3. Access chat at http://localhost:5173
```

## When to Use This Skill

- User wants to add a feature to existing project
- User says "enable modules" or "add auth"
- User wants to upgrade from one tier to another
- User asks "how do I add [feature]"

## Check Current Config

Before adding features, check what's currently enabled:

```bash
mozaiks info
```

This shows:
- Current preset
- Enabled features
- App name and auth status

## Example Interaction

**User:** "I want to add modules to my chatbot"

**You:** "You can enable modules with:

```bash
mozaiks add modules
```

This will:
1. Update platform/app.json to enable the `modules` feature
2. Allow you to create business logic in platform/modules/

After running this command:
1. Create a module directory: `platform/modules/my_module/`
2. Add `module.json` with name, category, author
3. Add `handler.py` with FastAPI routes
4. Restart your backend to load the new module

Modules are great for deterministic business logic that doesn't need AI. For AI tasks, use workflows instead."
