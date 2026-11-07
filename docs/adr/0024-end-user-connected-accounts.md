# ADR 0024: End-User Connected Accounts

Date: 2026-11-07

Status: proposed

## Decision

Mozaiks apps let each end user connect their own third-party account (Google
first) for plugins to use. The runtime owns the OAuth flow, grant storage and
token refresh. Plugins declare the connection they need and receive a
short-lived token only inside that user's own dispatch, limited to the
declared scopes. The app's own admins decide which declared connections the
app offers; platform operators have no path to a user's grant.

## Reason

Apps that work with a user's calendar or mailbox need the user's delegated
access, not an app-wide key. Without a runtime mechanism, each generated app
would write its own OAuth routes, token tables and refresh logic. That code is
easy to get wrong: tokens get stored in plain records, flows go unbound to a
browser, and scopes are over-requested. Users expect to connect accounts the
way they add connectors in other tools: from their own settings, revocable at
any time.

## Alternatives Considered

- **Let each plugin implement OAuth.** Rejected: it multiplies the most
  security-sensitive code in every app, and gives no consistent place for
  users to see and revoke grants.
- **Store the grant on the workspace or app connector.** Rejected: workspace
  connectors are app-wide credentials. A user's grant must never be usable for
  another user.
- **Let server-owned work (event reactions, operator dispatch) act with a
  user's grant.** Rejected for now: it makes a grant usable without the user
  present. A future delegated, consented background mode needs its own ADR.
- **Static file policy for the offering.** Rejected: the app's admins need to
  stop offering a connection at runtime without a redeploy.

## Consequences

- New public contract: `contracts/account_connections.yaml`
  (`mozaiks.account_connections.v1`), registered at app and module scope in
  the layout registry.
- New error code `ACCOUNT_CONNECTION_REQUIRED` (HTTP 403 from the module
  route) with `extra_data` that names the connection, the reason and an
  in-app `connect_route`.
- New host routes: `/api/me/connected-accounts*` (the signed-in user only),
  `/api/connected-accounts/callback` (unauthenticated, bound to the starting
  browser by a `__Host-` cookie and a single-use state), and
  `/api/admin/connected-accounts*` behind the existing `/api/admin` gate.
- The connector vault gains a `user` scope. Grants are stored under an opaque
  digest of the app and user, so no user id appears in secret names.
- A built-in owner-only profile page (`connected-accounts`), an admin Settings
  panel, account deletion and export integration, and audit kinds
  `account.connected`, `account.disconnected`, `account.connection.fail`. The
  admin offering writes `admin.write`.
- Token authority is limited to `authenticated_user`, `workflow` and
  `local_development` dispatches whose actor is the user. Plugins can be more
  restrictive: the Gmail plugin sends only on the user's own request.
- Google is a provider definition. Adding a provider means adding its
  endpoints and client settings, without changing plugins or routes.
- Providers that share one OAuth client across apps share one grant per user
  at the provider. Disconnecting in one app revokes that grant for the others
  too. Each app should use its own OAuth client.

## Reversibility

Medium risk: the declaration schema, error code and routes become public
contracts that generated apps and plugins depend on. Changing them needs a
versioned schema and a migration note.

## Affected Invariants

- Module business logic stays in modules: plugins call providers through
  `account_request`, and the runtime holds no plugin logic.
- No secrets in records or logs: refresh tokens live only in the vault, access
  tokens only in process memory, and neither appears in errors, audit or
  exports.
- The platform operator boundary is unchanged: operator dispatch cannot obtain
  a user's token, and no operator route returns one.

## OSS Boundary

Keep OSS: foundational. Every generated app needs the same mechanism, and it
names no hosted product. A hosted platform may supply a managed OAuth client
and verification, but it uses this contract.

## Validation

- Unit and route tests for declarations, the flow (PKCE, binding cookie,
  single use, partial grants, revocation), the offering, per-connection token
  scoping, dispatch authority, account deletion and export.
- Plugin packs exercised end to end: the real module loader, executor and
  module route against an emulated Google.
- Real-MongoDB tests for single-use flow claims under concurrency, unique
  records, TTL indexes and vault encryption.
- Browser tests for the profile tab and the admin panel.
