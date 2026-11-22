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

DesignDocs first asks `DesignPackSelectionAgent` for a list of pack IDs. That
turn sees the app concept and a compact catalog. The deterministic selector
checks the requested IDs, dependencies and source digests. An invalid choice
returns to the selection step before the Factory spends a turn writing the
design.

`DesignDocsAgent` receives the verified contracts for the chosen packs.
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
pack identities, action grants and access to each proposed page. Every new
page declares `requiresAuth`. The review shows whether visitors can open its
route anonymously, whether it requires sign-in and which verified roles it
requires. This review also covers page-only apps with no packs or grants.
AppGenerator uses the reviewed DesignDocs artifact. Requested changes return
to the owner for a new review.
