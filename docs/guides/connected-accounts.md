# Connected Accounts

Connected accounts let the people who use your app connect **their own**
third-party accounts, such as Google Calendar or Gmail, so plugins can work
with them. Each user decides whether to connect. The app's admins decide which
connections the app offers. A plugin only ever acts for the user who is making
the request, with only the access its declaration lists.

This is different from [Integrations](integrations/01-overview.md), which
connect a service once for the whole app (an LLM key, an email provider). A
connected account belongs to one user.

## What users see

When an installed plugin declares a connection, the profile page gets a
**Connected accounts** tab at `/me?tab=connected-accounts`. It lists the
connections the app offers, with the user's status for each:

| Status | Meaning |
|--------|---------|
| Not connected | The user can connect it. |
| Connected | The plugin can work with the user's account. |
| Reconnect needed | The user revoked access at the provider, or the grant expired. |
| Not available yet | The app's OAuth client for the provider is not configured. |

**Connect** opens the provider's consent screen in a popup. The user signs in
there, never in your app, and the app never sees their password.
**Disconnect** revokes the grant at the provider and removes it from the app.
Deleting the user's account does the same for every grant they gave.

When a plugin action needs a connection the user has not made, it fails with
`ACCOUNT_CONNECTION_REQUIRED` (HTTP 403). The error carries the connection's
label, a message and `connect_route`, which links to the tab with that
connection highlighted:

```json
{
  "detail": {
    "error_code": "ACCOUNT_CONNECTION_REQUIRED",
    "error": "Connect your Google Calendar account to use this.",
    "extra_data": {
      "connection_id": "google_calendar",
      "provider": "google",
      "label": "Google Calendar",
      "reason": "not_connected",
      "recovery_message": "Connect your Google Calendar account to use this.",
      "connect_route": "/me?tab=connected-accounts&connect=google_calendar"
    }
  }
}
```

`reason` is `not_connected`, `reconnect_required`, `missing_scopes` or
`unavailable`. Declarative pages show the message. Custom pages use the helpers
in the generated `ui/lib/moduleApi.js`:

```javascript
import { moduleAction, isAccountConnectionRequiredError, accountConnectionPath } from '../../lib/moduleApi.js'

try {
  await moduleAction('google_calendar', 'list_upcoming_events', {})
} catch (err) {
  if (isAccountConnectionRequiredError(err)) {
    setNotice({ message: err.message, href: accountConnectionPath(err) })
    return
  }
  throw err
}
```

## What the app's admins control

The admin portal's **Settings** page has a **Connected accounts** panel, backed
by `GET /api/admin/connected-accounts` and
`PUT /api/admin/connected-accounts/{connection_id}`. These routes use the same
gate as the rest of `/api/admin`: the `admin` role, or a verified email on the
`app.json` admins list.

For each declared connection, admins see:

- whether the app offers it, with a switch to stop or resume offering it;
- whether the app's OAuth client is configured, and the setting names to
  configure (never their values);
- how many users are connected, and how many need to reconnect;
- the redirect URI to register with the provider.

Admins never see who connected, which account they used, or any token.
Stopping a connection blocks new connections and plugin access at once.
Existing grants stay with their users, who can still see and disconnect them.
Every change is audited as `admin.write`.

## Configure the Google OAuth client

The Google provider reads the app's OAuth client like any app secret: from the
environment, or from the app's declared Key Vault references
(`security/secrets.yaml`).

| Setting | Purpose |
|---------|---------|
| `GOOGLE_ACCOUNTS_CLIENT_ID` | OAuth client ID (Web application). |
| `GOOGLE_ACCOUNTS_CLIENT_SECRET` | OAuth client secret. |
| `MOZAIKS_CONNECTED_ACCOUNTS_REDIRECT_URI` | Optional. The exact redirect URI registered with Google. Set it behind any proxy. |

These are deliberately separate from `GOOGLE_CLIENT_ID`, which signs users in
with Google.

1. In Google Cloud Console, enable the APIs your plugins use: Google Calendar
   API, Gmail API.
2. Configure the OAuth consent screen: app name, support email, and the scopes
   your plugins request.
3. Create an OAuth client ID of type **Web application**. Add the redirect URI
   shown in the admin panel. It is
   `https://<your app>/api/connected-accounts/callback` unless you set
   `MOZAIKS_CONNECTED_ACCOUNTS_REDIRECT_URI`.
4. Set the two client settings and restart the app.

Google treats Calendar and `gmail.send` as **sensitive** scopes. Until Google
verifies your app, only the test users listed on the consent screen can
connect. Gmail read and modify scopes are **restricted**: they need a security
assessment, so the Gmail plugin is send-only.

## Plugins

Two capability packs ship plugins built on connected accounts:

| Pack | Module | Connection | Scope | Actions |
|------|--------|------------|-------|---------|
| `google_calendar` | `google_calendar` | `google_calendar` | `calendar.events` | `list_upcoming_events`, `create_event` |
| `gmail` | `gmail` | `gmail_send` | `gmail.send` | `send_email` |

`send_email` sends only on the user's own request. A workflow (an AI agent in
the user's session) may draft a message for the user to send, but cannot send
it. To allow agent sends anyway, override `send_email` in the module's
workspace `handler.py`.

### Write a plugin

Declare the connection in the module's
`contracts/account_connections.yaml`:

```yaml
schema_version: mozaiks.account_connections.v1
connections:
  - id: google_calendar
    provider: google
    label: Google Calendar
    description: See your upcoming events and add new events to your Google Calendar.
    scopes:
      - https://www.googleapis.com/auth/calendar.events
```

Request the narrowest scopes that work. Two modules cannot declare the same
connection id. An invalid declaration disables every connection in the app
until it is fixed; the admin panel shows the error.

Call the provider as the requesting user from an action handler:

```python
from mozaiksai.core.connected_accounts import account_request, google_api_base_url

async def list_upcoming_events(self, ctx, **params):
    response = await account_request(
        ctx, "google_calendar", "GET",
        f"{google_api_base_url()}/calendar/v3/calendars/primary/events",
        params={"singleEvents": "true", "orderBy": "startTime"},
    )
    ...
```

`account_request` attaches an access token limited to the connection's scopes,
and retries once with a fresh token if the provider answers 401. If the user
has not connected, it raises `AccountConnectionRequired`, which becomes the
`ACCOUNT_CONNECTION_REQUIRED` error above. For a client library that wants the
token itself, use `account_access_token(ctx, connection_id)`.

Do not store tokens, write OAuth routes, or ask users for passwords or API
keys. The platform runs the OAuth flow, stores the grant and refreshes tokens.

## Who can use a grant

A token is issued only inside the user's own dispatch:

| Dispatch | Gets the user's token |
|----------|-----------------------|
| The user's HTTP request (`authenticated_user`) | Yes |
| A workflow in the user's own session (`workflow`) | Yes |
| Local development with authentication off | Yes |
| Anonymous visitors (`public_http`) | No |
| Event reactions, framework, app-internal and operator work | No |

The dispatch must also act for the same user (`actor_id` equals `user_id`), in
the app the host loaded. Platform operators have no route to a user's grant.

## Storage

- The refresh token is kept in the connector vault (Azure Key Vault, or
  encrypted Mongo for self-hosting) under an opaque id, never in a database
  record.
- `ConnectedAccounts` records which user connected which provider, the email
  the provider verified, and the scopes granted.
- `ConnectedAccountFlows` holds a flow in progress for at most ten minutes. The
  PKCE verifier is removed when the flow ends.
- `ConnectedAccountSettings` records which connections the app's admins
  withheld.
- Access tokens live only in process memory.

## Test without Google

Set `MOZAIKS_GOOGLE_EMULATOR_URL` to a local server that implements Google's
OAuth endpoints (`/o/oauth2/v2/auth`, `/token`, `/revoke`) and the APIs your
plugins call (`/calendar/v3/...`, `/gmail/v1/...`). The setting is refused in
any deployed environment.
