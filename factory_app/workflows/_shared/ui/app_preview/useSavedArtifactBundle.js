import { useEffect, useState } from 'react';
import { studioFetch } from '../../../../app/admin/pages/studioApi.js';

export function useSavedArtifactBundle(targetAppId, buildRegistryId, artifactVersionId, refresh = 0) {
  const [loaded, setLoaded] = useState(null);
  const requestKey = JSON.stringify([targetAppId, buildRegistryId, artifactVersionId, refresh]);
  useEffect(() => {
    if (!targetAppId || !buildRegistryId || !artifactVersionId) return undefined;
    let cancelled = false;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await studioFetch(
          `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/bundle?build_registry_id=${encodeURIComponent(buildRegistryId)}`,
          { signal: controller.signal },
        );
        const body = await response.json().catch(() => null);
        if (!response.ok) throw new Error(body?.detail || `Saved build unavailable (${response.status}).`);
        const workbench = body?.workbench;
        if (body?.artifact_version_id !== artifactVersionId || body?.app_id !== targetAppId
          || body?.build_family !== 'app_bundle' || workbench?.build_family !== 'app_bundle'
          || workbench?.artifact_version_id !== artifactVersionId || workbench?.target_app_id !== targetAppId
          || workbench?.build_registry_id !== buildRegistryId || body?.review?.artifact_version_id !== artifactVersionId
          || body?.review?.app_id !== targetAppId || body?.review?.artifact_kind !== 'app_bundle') {
          throw new Error('The saved build response does not match the selected app and version.');
        }
        if (!cancelled) setLoaded({ requestKey, body });
      } catch (error) {
        if (!cancelled) setLoaded({ requestKey, error: error instanceof Error ? error.message : 'Saved build unavailable.' });
      }
    }
    load();
    return () => { cancelled = true; controller.abort(); };
  }, [targetAppId, buildRegistryId, artifactVersionId, requestKey]);
  return loaded?.requestKey === requestKey ? loaded : { body: null, error: null };
}
