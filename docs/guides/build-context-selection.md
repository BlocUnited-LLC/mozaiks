# Selecting shipped build-context packs

The shipped Factory build-context directory is a catalog of available packs.
When it is also the active build-context root, the trusted loader projects pack
descriptors and contracts only for IDs in the operator's `operator_capabilities`
selection. An empty or absent selection projects no shipped packs; a partial
selection does not include the remaining catalog entries. Catalog files remain
available for discovery. Non-pack build-context projections still apply.

An operator-owned workspace build-context root retains its registration
semantics: a pack declared there is admitted through its applicable workflow
projection. Explicit selections may also resolve shipped packs by exact ID. See
the [build-context pack contract](../architecture/workflows/build-context-packs.md)
for source verification and selection authority.

Social version `0.1.18` and Messaging version `0.1.17` add DesignDocs to their
workflow registrations. Pack digests include `context.yaml`, so earlier pinned
versions and digests do not identify these new bytes. Existing pinned selections
must be deliberately updated and revalidated against the new pack identity;
the loader must not silently substitute a new digest for an old pin.

## Unreleased change

Factory catalog entries no longer become selected packs merely because the
shipped catalog is the active build-context root. Select shipped pack IDs
explicitly. Social and Messaging now support DesignDocs with the versions
listed above; selections pinned to previous pack identities require review.

## How the Factory uses a pack

Four app-type recipes help the selection agent compare the approved requirements
with available packs. A store with a cart and orders can use `commerce`. A
community with member posts or friendships can use `social`. Direct and group
conversations can use `messaging`. The SaaS billing recipe applies when the app
explicitly chooses operator-enabled `mozaikspay` for paid subscriptions.

Each recipe pairs a pack ID with the behavior it supplies. The agent can select
multiple packs or none. The app owns its brand, navigation, and pages outside
the selected pack's declared outputs. For example, a store can add shipment
tracking to an order, and a community can add an appeals workflow for moderators.
The recipes live in `factory_app/build_context/AppGenerator/domain_catalogs.yaml`.

DesignDocs first asks `DesignPackSelectionAgent` for a list of pack IDs. That
turn sees the app concept and a compact catalog. For a revision it also receives
the verified reusable pack identities and a bounded inventory of modules and
actions from the accepted app. The selector uses the requested change to decide
which capabilities to reuse, extend or add. The deterministic selector
checks the requested IDs, dependencies and source digests. An invalid choice
returns to the selection step before the Factory spends a turn writing the
design.

`DesignDocsAgent` receives the verified contracts for the chosen packs.
`source_capability_packs` on a design surface describes capability categories.
Reusable pack identities come from the verified selection. An approved agentic
capability can use an app-owned workflow with an empty pack selection.
AppGenerator reads module archetypes from the saved app build plan and workflow
event bindings from the corresponding workflow bundle.
For revisions the design agent also sees a bounded projection of the accepted
app archive. It lists module-owned collections, record and ownership keys,
typed fields, schema-page paths and access gates, app roles, and module action
signatures. Canonical HTTP write operations come from actions present in that
archive. Their editable fields come from closed inputs and exclude record keys
and managed fields. An open or unsupported input leaves its editable field
list unknown. This context guides the proposed design. Owner review and the
existing save gates still determine the approved contract.
For each pack-owned module collection the prompt identifies its pack, module,
collection, entity and tenancy. For example, Content supplies the `entries`
collection in module `content` with entity `ContentEntry`. A recipe page and a
portfolio page can both use those records with different layouts and labels.
Design save composes the collection from the pack. An editor grant references
`entries` and the relevant shipped actions. Apps can declare separate
collections for additional records they own. When packs supply all app data,
the design leaves `data_contract.surfaces` and `shared_collections` empty and
declares its presentation in the surface map and experience spec.
Template files stay on disk for deterministic assembly. The owner reviews the
pack identities alongside the proposed permissions and page roles. AppGenerator
uses the selection recorded in the reviewed DesignDocs artifact.

When the owner requests changes, the replacement returns for review even if it
removes every pack, grant and page-role requirement. An initial design with
empty selections, grants and page gates can still proceed directly.
