# Platform Navigation Contract

Mozaiks uses one navigation ownership model across Studio, generated apps, and
hosted product workspaces. The same rule should hold at any scale: identify the
object being managed, then route to the surface that owns that object.

## Canonical Ownership

| Surface | Owns | Examples | Must not own |
| --- | --- | --- | --- |
| Profile | The signed-in person | identity, avatar, display name, email, personal preferences, personal social graph, personal invitations, personal votes and delegations | app builds, app access, app billing, deployments, workspace settings, team or org operations |
| Admin Portal | An app, workspace, team, or org | access, roles, usage, health, integrations, billing, governance, collaborators, revenue participation, audit, deployment and domain posture | personal identity, personal preferences, direct app creation |
| Studio | Build lifecycle and generated artifacts | create app, import app, build runs, artifact review, promotion, refinement, build history | user profile data, app runtime business behavior, hosted-only provider operations |
| App Shell | The app's domain work | dashboards, module pages, customer workflows, operational records | account identity, Studio build state, app/workspace administration |
| Chat Route | A specific conversation or workflow session | direct `chat_id` resume, live workflow interaction, replay of a selected session | global create intent, app portfolio management |

## Create And Resume Semantics

Creation and continuation are separate product intents.

- Shell `Create App`, `/create`, and any Create shortcut always start a fresh
  build journey.
- Continue/resume belongs in Studio or Admin Portal app/build history, where the
  user can see the app record, build status, last artifact, and exact session
  being resumed.
- A direct `/chat?...&chat_id=...` URL resumes that exact chat.
- A bare workflow URL may start a new workflow only when it carries fresh-start
  intent such as `new=1`, `fresh=1`, or `force_new=1`.
- The global or persistent chat widget may reopen the current conversation, but
  it must not override explicit Create intent.

This keeps browser storage, recent chat fallback, and runtime reconnect logic
from changing the meaning of the Create entrypoint.

## Profile Boundary

Profile is an account surface. It is intentionally small.

Profile may render:

- framework identity fields from `GET/PUT /api/me`
- personal preferences from `GET/PUT /api/me/preferences`
- user-scoped module panels declared in `contracts/profile.yaml`
- personal relationship inventory such as invitations, communities, votes, or
  delegations when the relationship follows the user across apps

Profile must not render app/workspace management. Billing plans, subscriptions,
entitlements, collaborators, deployments, build runs, app access, audit logs, and
team/org settings belong in Admin Portal or Studio.

## Admin And Studio Boundary

Admin Portal owns durable app/workspace operations. Studio owns build lifecycle.
They can link to each other, but they do not replace each other.

- Use Studio for build runs, generated artifacts, refinement, promotion, and
  unfinished app creation.
- Use Admin Portal for app access, roles, health, usage, integrations,
  governance, billing, and deployment posture.
- Use App Shell for the app's normal domain workflow once the app is running.

## Generator Requirements

Generators must not create duplicate surfaces for these platform-owned areas.

- Do not scaffold replacement `/profile`, `/admin`, or Studio build-history
  pages inside generated app UI.
- Do not put Create/continue behavior into profile panels.
- Do not route app-management pages through the app domain shell unless they are
  app-owned business pages rather than management surfaces.
- When an app concept needs billing, roles, integrations, deployment, or build
  continuation, emit module/admin contracts or Studio metadata instead of
  profile UI.

## Runtime And Frontend Contract

The platform shell treats Create as a fresh-start action:

- shell Create actions navigate to `/create?new=1`
- the `/create` entrypoint declares `meta.freshStart: true`
- transition routes use fresh-start intent only for the entry route; after the
  transition creates a concrete chat session, the final workflow chat URL keeps
  only `mode=workflow`, `workflow`, and the new `chat_id`
- `ChatPage` ignores stored active chat state when the URL carries `chat_id` or
  fresh-start intent
- API chat start forwards `force_new` when the URL asks for a fresh workflow

Resume remains explicit through a selected `chat_id`, app/build history, or
current-conversation widget behavior.
