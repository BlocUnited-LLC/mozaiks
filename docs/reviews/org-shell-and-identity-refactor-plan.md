# Org Shell And Identity Refactor Plan

## Purpose

This repo should stop treating profile, workspace management, and app shell as
one blended surface. The target model is deterministic and scope-driven:

- `Profile` is the signed-in person.
- `Studio` is the workspace/org management shell.
- `App Shell` is one branded app/product.

The repo is not in production, so the clean replacement wins over
preservation.

## Problem Statement

The current UI and shell composition still leak management affordances across
surfaces:

- profile menus can surface workspace-management items
- app shells can inherit studio-style controls
- the same account surface can read as if it belongs to every app

That creates a vague UX and makes shell composition feel heuristic instead of
deterministic.

## Target Model

### 1. Person

The person surface is a normal account profile:

- identity
- avatar
- display name
- bio
- personal preferences
- user-scoped module tabs and panels

### 2. Studio

The org/workspace shell is the management base for a portfolio of apps:

- app directory
- workspace branding
- workspace settings
- aggregate usage
- team/member management
- shared integrations
- app-level entrypoints

Studio is the branded home base. It is not a person profile.

### 3. App Shell

Each app gets its own branded shell:

- app overview
- build and revision
- app usage
- app operations
- app integrations
- app users
- app admin

## Deterministic Rules

1. Select the surface first.
2. Compose only the contracts for that surface.
3. Add module contributions only where the contract allows them.
4. Never infer a cross-surface menu item from the presence of a module or an
   admin role alone.
5. Never inject Studio items into a customer app shell unless the app shell
   contract explicitly allows it.

## Workstream Split

### Codex Ownership

Doc and terminology alignment only:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/architecture/foundations/platform-terminology-and-brand-language.md`
- `docs/architecture/foundations/platform-information-architecture.md`
- `docs/architecture/app/platform-navigation-contract.md`
- `docs/architecture/app/tenant-auth-and-scope.md`
- `docs/architecture/foundations/profile-panel-contract.md`

### Claude Code Ownership

Runtime and shell-composition refactor only:

- explicit surface selection in shell composition
- removal of cross-surface menu injection
- app-shell vs studio-shell route and menu separation
- profile surface cleanup so app shells do not expose workspace tools by default

The two workstreams should not edit the same files.

## Acceptance Criteria

- `Profile` never reads as an org/workspace surface.
- `Studio` is the org/workspace shell and can carry branding.
- `My Apps` appears only in the Studio/workspace context.
- App shells keep their own branded navigation and admin affordances.
- Shell output is deterministic for a given surface and config set.

