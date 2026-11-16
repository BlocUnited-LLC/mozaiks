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
