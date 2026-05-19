# Platform Terminology And Brand Language

Mozaiks needs four separate language layers:

1. customer-facing product language
2. restrained branded language
3. internal engineering language
4. runtime and operational language

The purpose of this split is clarity. Users should navigate Mozaiks through
plain product terms, while engineering keeps the precise internal vocabulary
needed to build and operate the system.

## Principles

- Prefer enterprise clarity over clever naming.
- Use branding sparingly and structurally, not as the primary IA.
- Keep runtime and orchestration terms out of normal customer UX.
- Do not expose repo-local or dogfooding names in product copy.
- Use one canonical name per visible concept.

## Brand Tone

The Mozaiks tone should feel:

- operational
- intelligent
- composed
- exploratory
- enterprise-grade
- subtly futuristic

Avoid:

- game-like vocabulary
- mascot language
- cartoon sci-fi
- excessive astronaut or galaxy metaphors
- lore-driven navigation labels

The closest language family is:

- console
- build
- deploy
- operations
- integrations
- workspace
- runtime

## Language Layers

| Layer | Audience | Purpose | Examples |
| --- | --- | --- | --- |
| Customer-facing product language | end users, admins, operators | navigation, UI copy, onboarding | `Apps`, `Build`, `Deploy`, `Usage`, `Operations`, `Integrations`, `Admin` |
| Restrained branded language | product marketing, onboarding, shell descriptors | light thematic cohesion | `Mozaiks Console` |
| Internal engineering language | runtime contributors, framework engineers | implementation boundaries and repo ownership | `factory_app`, `Studio host`, `workflow_sequence` |
| Runtime and operational language | runtime engineers, observability surfaces, internal docs | transport, orchestration, persistence, telemetry | `control_plane`, `artifact_version`, `revision_context`, `journey` |

## Customer-Facing Vocabulary

These are the preferred visible terms.

| Term | Use For | Notes |
| --- | --- | --- |
| `Mozaiks` | the overall product | product name |
| `Mozaiks Console` | shell descriptor in onboarding/help/marketing | not required as a nav item |
| `Workspace` | the tenant/account context | not the name of the app builder |
| `Apps` | the multi-app directory and default landing area | canonical workspace landing area |
| `App Console` | the single-app management context | prose and IA term, not necessarily a literal heading on every screen |
| `Build` | app creation and revision surface | canonical creation and revision surface |
| `Deploy` | hosting, rollout, environments, release state | separate from build |
| `Usage` | spend, tokens, API consumption, aggregate metrics | use for measured consumption |
| `Operations` | health, incidents, runtime failures, deployment status | broader and clearer than `Activity` |
| `Integrations` | connected systems and service configuration | canonical term for connected services |
| `Users` | app-level or workspace-level user management | context determines scope |
| `Settings` | configuration and defaults | use instead of custom nouns |
| `Admin` | privileged management surface | user-facing name for the admin shell |

Host apps may add commercial terms such as billing when they expose a
host-owned capability. The OSS factory console does not reserve or hardcode a
billing route.

## Restrained Branded Language

Only a small amount of thematic language should remain visible.

Allowed:

- `Mozaiks Console`
- launch-oriented or operational subtitles in empty states and onboarding
- subtle language around coordination, orchestration, and readiness

Not recommended as primary navigation:

- `Hub`
- `Navigator`
- `Atlas`
- `Fleet`
- `Treasury`
- `Mission`
- `Command` as a nav label

`Console` is the preferred branded structural term because it feels operational
and premium without obscuring the product function.

## Internal Engineering Vocabulary

These terms should remain internal to engineering docs, repo structure, logs, or
implementation contracts.

| Internal Term | Meaning |
| --- | --- |
| `factory_app` | first-party workspace that co-locates the app bundle, builder workflows, and first-party harness pack |
| `Studio` | host/composition term for the management interface layer |
| `Studio host` | `mozaiksai.hosts.studio` runtime composition |
| `Control Plane` | runtime orchestration and refinement routing subsystem |
| `workflow_sequence` | internal workflow-sequencing contract |
| `journey` | internal workflow/router execution unit |
| `artifact_store` / `artifact_version` | runtime persistence concepts |
| `revision_context` | runtime-owned revision and lineage assembly |

## Runtime And Operational Vocabulary

These terms are correct inside logs, observability, internal admin diagnostics,
and runtime docs, but should not become general product IA.

- control plane
- session router
- workflow trigger
- orchestration runtime
- artifact lineage
- deferred harness decision
- refinement scope
- revision intent

If one of these concepts must become user-visible, translate it first into a
product term. Example: expose `Build review` or `Needs revision`, not
`control-plane decision`.

## Terms To Retire From UX

Do not use these as visible product concepts:

- `Hub`
- `Studio` as a top-level product area
- `Factory App`
- `Control Plane`
- `workflow_sequence`
- `Adapters`

Preferred replacements:

- `Hub` -> `Apps`
- `Studio` -> `Build`
- `Adapters` -> `Integrations`
- `Activity` -> `Operations` when the scope is health, incidents, runtime state, or deployment state

## Documentation Rule

Architecture and product docs should explicitly state which layer a term belongs
to when there is any chance of ambiguity.

Examples:

- "Studio is an internal host/composition term."
- "Hosting is the customer-facing surface for managed rollout posture."
- "Control Plane is runtime infrastructure, not a customer-facing dashboard."

## Product Copy Rule

Use the product term in visible UI even when the implementation name differs.

Examples:

- visible: `Integrations`
- internal: `AppIntegrationsPage`

- visible: host-provided commercial section
- internal: host-owned route/component name
