# Workspace Pages

Workspace pages give you a view across all your apps. They are not tied to any
one app — use them to manage your full portfolio and shared setup.

## Apps

**Route:** `/apps`

The home page of your workspace. Shows every app you have created, its current
state, and whether it needs attention. Apps that need your input surface at the
top.

From here you can:

- **Create App** — start a new app build
- **Continue Build** — resume an in-progress build on an existing app
- **Open an app's dashboard** — jump into usage, access, or app settings

## Usage

**Route:** `/usage`

Workspace-level token and cost summary across all apps. Shows spend, token
volume, and chat counts over time, broken down per app. Click an app row to
drill into its workflow and chat-level usage detail.

## Integrations

**Route:** `/integrations`

Shared provider setup for the workspace. Shows which services are already
connected, which apps need a provider that is not yet configured, and which
additional providers are available to add.

Configure a credential here once and every app that declares that service can
use it. Secret values are never shown — only connection status.

## Support

**Route:** `/support`

A cross-app view of support conversations. Shows which apps have open support
chats and how many need a reply. Select an app to open its per-app support queue.
