# Canonical Studio demo fleet

Factory Studio mock mode uses three reference apps. The fleet is intentionally
small so every workspace and app-admin surface can exercise the same identities
instead of accumulating unrelated placeholder rows.

| App | Canonical archetype | Primary portal coverage |
| --- | --- | --- |
| Campaign Revision Workbench | `authenticated_crud_projects` | overview, branding, access, settings, support |
| Partner Delivery Studio | `admin_operations_dashboard` | building, launch, integrations, governance |
| Member Growth Studio | `monetized_saas_reports` | billing, usage, access, activity |

All three records represent accepted, runnable archetypes from the generated-app
acceptance matrix. Demo usage, billing, deployment, users, activity, workflows,
runs, sessions, connectors, and artifact history must stay closed over these
same three app IDs. A new demo app is therefore a fleet-contract change, not a
one-page fixture addition.

`mozaiks-app` is not embedded as one of these records. App Zero is a real,
proprietary consumer workspace and is tested in its own repository. Hardcoding
it into the OSS Factory fixture would couple the framework to a sibling checkout
and blur the hosted-product boundary.

