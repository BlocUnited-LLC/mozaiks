# App Architecture

App architecture docs define the generated app workspace contract: what lives in
an app workspace, how app pages/modules/config/backend/brand and root workflows fit together, and how
app-owned surfaces extend the platform.

Read these docs when changing app workspace structure, page/admin authoring,
app manifests, app lifecycle, or app-owned service surfaces.

| Doc | Scope |
| --- | --- |
| [Generated App Lifecycle](generated-app-lifecycle-model.md) | App lifecycle states and promotion model |
| [Canonical App Structure](canonical-app-structure.md) | App workspace shape for config, backend, modules, pages, root workflows, and brand |
| [App Manifest and Platform Targets](app-manifest-and-platform-targets.md) | App manifest fields and platform target semantics |
| [App Bundle Declaratives](app-bundle-declaratives.md) | Declarative app artifact families |
| [Platform Authoring](platform-authoring.md) | Rules for app, page, admin, and platform authoring |
| [Surface Model](surface-model.md) | Page, workflow, and module surface ownership |
| [UI Surface and Layout Architecture](ui-surface-and-layout-architecture.md) | Shell, pages, workflow UI, and layout boundaries |
| [App Dashboard Contract](app-dashboard-contract.md) | Canonical Workspace/App Dashboard manifest, portal lanes, panels, and workflow-launch boundaries |
| [Admin System](admin-system.md) | Admin surface ownership and contract |
| [Platform Navigation Contract](platform-navigation-contract.md) | Canonical ownership for Profile, Admin Portal, Studio, App Shell, Create, and resume |
| [Account, Admin, and Platform Services](account-admin-and-platform-services.md) | Account/admin/platform service boundaries |
| [User Classes and Resource Relationships](user-classes-and-resource-relationships.md) | Host-agnostic generated-app pattern for durable user classes, memberships, route authorization summaries, policy hooks, and resource relationships |
| [Usage and Token Display](usage-and-token-display.md) | How AI token usage and wallet balance are surfaced to end users, app operators, and platform creators — and where the OSS/hosted boundary sits |
