---
name: add-branding
description: Customize app shell branding, navigation, logos, fonts, and theme tokens in a Mozaiks app workspace.
argument-hint: "[branding or shell change]"
disable-model-invocation: true
---

<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->
Complete this branding task: $ARGUMENTS

1. Read `AGENTS.md`, `CLAUDE.md`, and `.claude/rules/frontend.md`.
2. Prefer declarative config over custom React.
3. Use `app/brand/theme_config.json` for colors, typography, spacing, density, radius, assets, and identity tokens.
4. Use `app/brand/assets/` for logos, icons, and images.
5. Use `app/brand/fonts/` for local font files.
6. Use `app/config/shell.json` for shell navigation, chrome policy, shortcuts, profile menu, notifications, and footer behavior.
7. Use route-level page metadata for page-owned navigation and `shell_mode` whenever possible.
8. Keep workflow startup settings in `app/config/ai.json`; do not mix them into brand or shell files.

Canonical files:

- `app/app.json` owns app identity, auth flags, admins, and startup landing spot.
- `app/brand/theme_config.json` owns visual identity.
- `app/config/shell.json` owns shell chrome and navigation policy.
- `app/config/ai.json` owns AI startup and workflow entry behavior.

Rules:

- Do not invent `brand.json`, `ui.json`, `auth.json`, or alternate shell config files.
- Do not hardcode colors, font families, or app-specific visual constants in React when a theme token exists.
- Do not manually add platform-injected routes such as admin portal entries.
- Do not edit generated artifacts in `generated/` unless they are being intentionally promoted.

Verification:

1. Validate edited JSON/YAML.
2. Confirm referenced assets exist under `app/brand/`.
3. Run `.\scripts\run-studio.ps1 -DryRun` when available.
4. If the shell is running, check `/api/theme-config` and `/api/shell-config`.
<!-- END MOZAIKS MANAGED: agent-guidance -->
