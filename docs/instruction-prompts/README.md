# Instruction Prompts

This folder contains **comprehensive instruction prompts** designed to be used with AI coding agents (Claude Code, Cursor, GitHub Copilot, etc.).

## What This Is

When users encounter a task like "set up environment variables" or "create a new workflow," they can copy the relevant instruction prompt into their AI coding agent. The AI will then guide them through the entire process step-by-step.

**These are NOT simple one-liner prompts.** They're detailed, context-rich instructions that give the AI everything it needs to help the user succeed.

## How to Use

1. Find the instruction prompt that matches your task
2. Copy the path to the `.md` file
3. Tell your AI agent: "Read [path] and help me with [task]"
4. The AI will guide you through the task

Example:
```
I want to create a new Mozaiks workflow.

Please read the instruction prompt at:
docs/instruction-prompts/adding-workflows/01-overview.md

My workflow should be called: CustomerSupport
```

## Folder Structure

```
instruction-prompts/
├── README.md
│
├── getting-started/
│   ├── environment-variables.md       # Setting up .env
│   └── full-setup-from-clone.md       # Complete setup from git clone
│
├── databases/
│   └── setup.md                       # MongoDB + Keycloak setup
│
├── custom-brand-integration/
│   ├── 01-overview.md                 # Brand customization overview
│   ├── 02-brand-json.md               # Colors, fonts, logos
│   ├── 03-ui-json.md                  # Header, footer, menus
│   ├── 04-assets.md                   # Managing assets
│   ├── 05-wiring.md                   # How config connects
│   ├── 06-auth-json.md                # Login page styling
│   └── colors-and-theme.md            # Quick theme changes
│
├── adding-workflows/
│   ├── 01-overview.md                 # Planning workflows
│   ├── 02-backend-basics.md           # YAML configuration
│   ├── 03-tools.md                    # Adding tools
│   ├── 04-ui-components.md            # React components
│   └── 05-testing.md                  # Verification
│
├── workflows/
│   └── create-new-workflow.md         # Quick workflow guide
│
└── telemetry/
    ├── 01-overview.md                 # Telemetry overview
    ├── 02-agent-tracing.md            # OpenTelemetry setup
    ├── 03-cost-tracking.md            # Cost calculation
    └── 04-budget-management.md        # Spending limits
```

## Mapping: Guides ↔ Instruction Prompts

| Guide | Instruction Prompt |
|-------|-------------------|
| `guides/databases/01-overview.md` | `databases/setup.md` |
| `guides/databases/02-mongodb.md` | `databases/setup.md` |
| `guides/databases/03-keycloak.md` | `databases/setup.md` |
| `guides/databases/04-production.md` | `databases/setup.md` |
| `guides/adding-workflows/01-overview.md` | `adding-workflows/01-overview.md` |
| `guides/adding-workflows/02-backend-basics.md` | `adding-workflows/02-backend-basics.md` |
| `guides/adding-workflows/03-tools.md` | `adding-workflows/03-tools.md` |
| `guides/adding-workflows/04-ui-components.md` | `adding-workflows/04-ui-components.md` |
| `guides/adding-workflows/05-testing.md` | `adding-workflows/05-testing.md` |
| `guides/telemetry/01-overview.md` | `telemetry/01-overview.md` |
| `guides/telemetry/02-agent-tracing.md` | `telemetry/02-agent-tracing.md` |
| `guides/telemetry/03-cost-tracking.md` | `telemetry/03-cost-tracking.md` |
| `guides/telemetry/04-budget-management.md` | `telemetry/04-budget-management.md` |
| `guides/custom-brand-integration/01-overview.md` | `custom-brand-integration/01-overview.md` |
| `guides/custom-brand-integration/02-brand-json.md` | `custom-brand-integration/02-brand-json.md` |
| `guides/custom-brand-integration/03-ui-json.md` | `custom-brand-integration/03-ui-json.md` |
| `guides/custom-brand-integration/04-assets.md` | `custom-brand-integration/04-assets.md` |
| `guides/custom-brand-integration/05-wiring.md` | `custom-brand-integration/05-wiring.md` |
| `guides/custom-brand-integration/06-auth-json.md` | `custom-brand-integration/06-auth-json.md` |

## Design Principles

1. **Context is king** — Each prompt includes system context
2. **Step-by-step** — Clear, numbered steps
3. **Explicit paths** — No ambiguity about file locations
4. **Expected outcomes** — What success looks like
5. **Troubleshooting** — Common issues and fixes
6. **No assumptions** — Written for beginners

## Contributing

When adding a new instruction prompt:

1. Follow the template structure
2. Include context at the top
3. Use explicit file paths
4. Add verification steps
5. Include troubleshooting
6. Test with an AI agent
7. Add "!!! tip" block to matching guide
