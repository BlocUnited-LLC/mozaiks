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
verified the app's owner and any tenant/workspace scope. The action must
declare the `factory.build_target.register_self` permission, and a trusted
app worker must dispatch it with `ModuleDispatchAuthority(kind="app_internal",
permission_mode="enforce")`, a fixed actor, and that exact permission. The
registration seam checks the actual ModuleExecutor audit: the permission must
be both declared by the action and granted to the actor, and the audited app,
module, action, and actor must match the injected `ModuleContext`. Browser
requests, workflow calls, trusted bypass dispatches, and ad hoc contexts do
not meet this contract. Do not expose the internal action as a public module
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

An app can link this returned identity to its own scoped record after another
owner/tenant/workspace compare and swap. Subsequent source import, baseline
acceptance, refinement approval, workflow launch, and publication each retain
their own validation and authorization gates.
