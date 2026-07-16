# App Dashboard

App dashboard pages manage one app. The header always shows which app you are
viewing, its current lifecycle state, and what to do next.

## Overview

**Route:** `/apps/:appId/overview`

The main snapshot for an app: what it costs, who is using it, and what needs
attention right now. The app header shows the name, logo, tagline, and a single
lifecycle-aware next step — such as continue build, review artifacts, or
configure integrations.

Secondary panels link to deeper diagnostic and history pages, but the primary
action appears only once.

## Support

**Route:** `/apps/:appId/support`

Support conversations for this app, organized by status: **Needs reply**,
**In progress**, and **Resolved**. Open a conversation to reply to a user or
assign it to an operator.

## Access

**Route:** `/apps/:appId/access`

Who can use this app, what role they have, what plan they are on, and whether
anyone is blocked or needs attention. Shows account status, last activity, and
any access flags for each account.

## Usage

**Route:** `/apps/:appId/usage`

Token and cost detail for this app broken down by workflow and chat. Expand a
workflow group to see individual chats. When model pricing is incomplete, a
pricing status notice appears collapsed at the bottom.

## Diagnostic Pages

These pages are linked from Overview or Support when something needs
investigation. They are not listed in primary navigation.

| Page | Route | Opens from |
| --- | --- | --- |
| Health diagnostics | `/apps/:appId/health` | Overview when a runtime or integration issue needs investigation |
| Integration setup | `/apps/:appId/integrations` | Overview when a required service is not connected, or workspace Integrations |
| Build history | `/apps/:appId/activity` | Overview or Support when you need artifact versions or build audit detail |
