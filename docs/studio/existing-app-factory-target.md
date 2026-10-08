# Register the Running App as a Factory Build Target

An existing Mozaiks app can register its own fixed `app/app.json` identity in
Factory's owner-scoped AppRegistry. This is the first identity step for an app
that intends to use Factory workflows on its current codebase. Registration
does not import source, create a `BuildRecord`, start a workflow, or approve a
refinement.

The public Python call is
`mozaiksai.hosts.build_target_registration.register_existing_self_build_target(ctx)`.
It returns `ExistingSelfBuildTarget` with `build_registry_id`,
`target_app_id`, `execution_app_id`, and `owner_user_id`. The target and
execution IDs are the same validated `appId` loaded by the running platform
host. No caller-supplied target ID, repository, or source path is accepted.

Call it from an app-owned **internal** module action after that action has
checked a durable app record for the designated Factory owner, `app_id`, and
tenant/workspace. For a background job, recheck the original owner and scope
record when the job runs. The owner must be an authenticated subject that can
later reopen the same Factory target in Studio; `anonymous`, `system`, and a
separate service actor cannot claim it for that person. Do not take the owner
from a request parameter, a role label, or an unverified job payload.

The action must declare `factory.build_target.register_self`. A trusted app
worker dispatches it with `ModuleDispatchAuthority(kind="app_internal",
permission_mode="enforce")`, that owner as actor, and the declared permission.
The seam checks that the supplied authority, permission result, audit fields,
and loaded host identity agree. These are **consistency checks**, not proof
that ModuleExecutor issued the context: `ModuleContext`, `ModuleDispatchAudit`,
and `ModulePermissionCheck` are constructible Python dataclasses. An arbitrary
Python module running in the same process can supply matching fields or call
the executor with a fabricated internal request. This integration therefore
requires trusted app Python in the platform process. It does not isolate
untrusted generated or third-party Python from the Factory registry. That
deployment would need a separate trusted process with its own authenticated
authorization boundary before exposing this operation.

The documented call path does not use browser requests, workflow calls, or
trusted bypass dispatches. Do not expose the internal action as a public module
API or derive its authority from request parameters.

The AppRegistry claim is atomic and insert-only. Repeating the call for the
same owner, execution host, and target returns the same Factory registry ID
without changing lifecycle state, the current build, timestamps, or other
record fields. A competing owner or host binding fails. The global unique
`app_id` index allows one Factory owner for this target; applications with
multiple operators must choose one designated Factory owner before claiming
the target. Later Studio sessions must authenticate as that same Factory owner
to reopen or refine the target. A separate service actor cannot claim the
target for a different human owner. App-owned commercial registry IDs remain
separate from the Factory `build_registry_id`.

Trusted app code may pass `conflicting_target_app_ids=("known-old-id",)` when
an earlier build process used another target ID for the same loaded app. The
registration seam checks those IDs across Factory owners and refuses the new
claim while any is present. It returns no historical row details. IDs must be
distinct from the loaded app ID; the call accepts at most eight. This is a
preflight guard, not a migration or a substitute for the app-owned scoped
record check. It deliberately blocks while the historical row exists,
including when its lifecycle is `archived`: archival alone does not make that
lineage read-only for every Factory entry path. The existence check and new
target insert are separate operations, so the operator must also stop historical
target creation during reconciliation. Never fill this argument from a
browser request.

An app can link this returned identity to its own scoped record after another
owner/tenant/workspace compare and swap. Subsequent source import, baseline
acceptance, refinement approval, workflow launch, and publication each retain
their own validation and authorization gates.

## App Zero activation prerequisite

App Zero's current bootstrap writes a Factory target with
`app_id=mozaiks-platform-app-zero` and `chat_app_id=mozaiks-platform`. This
self-registration API instead uses the running host's validated `appId` for
both fields. Calling it as is would create a second Factory target, not reopen
the existing one. Before enabling the App Zero caller, reconcile the existing
Factory row and its commercial `factory_binding` to one canonical target and
confirm that the designated Factory owner is the owner of the scoped App Zero
record. The App Zero action must recheck that record's `app_zero`, `app_id`,
`owner_id`, `tenant_id`, and `workspace_id` before dispatch and before linking
the returned identity. Registration alone does not perform that reconciliation
or establish a brownfield baseline.
