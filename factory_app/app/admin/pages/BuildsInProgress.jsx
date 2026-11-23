import { useEffect, useMemo, useState } from 'react'

import { Panel } from '../../ui/components/StudioShared.jsx'
import { getAppDisplayName } from './appStudioModel.js'
import ProjectBuildButton from './ProjectBuildButton.jsx'
import { fetchRecentVersions } from './projectRoomApi.js'
import {
  buildsInProgressCandidates,
  projectAction,
  projectStatus,
  projectStatusSentence,
} from './projectRoomModel.js'

function formatDay(value) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value))
  } catch {
    return ''
  }
}

/**
 * Apps whose build is not finished, each with the same one-sentence status
 * and button as its Project page. One history request per listed app (two
 * when a full first page left out the app version); an app whose history
 * cannot be read is left out rather than shown with a guess.
 */
export default function BuildsInProgress({ apps }) {
  const { apps: candidates, more } = useMemo(() => buildsInProgressCandidates(apps), [apps])
  const key = candidates.map((app) => app.build_registry_id).join('|')
  const [loaded, setLoaded] = useState({ key: null, versionsById: {} })

  useEffect(() => {
    if (!key) return undefined
    const controller = new AbortController()
    Promise.allSettled(candidates.map((app) => (
      fetchRecentVersions(app.build_registry_id, { signal: controller.signal })
        .then((result) => [app.build_registry_id, result])
    ))).then((results) => {
      if (controller.signal.aborted) return
      setLoaded({
        key,
        versionsById: Object.fromEntries(
          results.filter((result) => result.status === 'fulfilled').map((result) => result.value),
        ),
      })
    })
    return () => controller.abort()
    // `key` names exactly the listed apps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (!key || loaded.key !== key) return null
  const rows = candidates
    .filter((app) => loaded.versionsById[app.build_registry_id])
    .map((app) => {
      const { versions, unknownFamilies } = loaded.versionsById[app.build_registry_id]
      const status = projectStatus({ versions, app, unknownFamilies })
      return { app, status, action: projectAction({ status, app, appId: app.app_id }) }
    })
  if (rows.length === 0) return null

  return (
    <Panel title="Builds in progress">
      <ul className="-my-3 divide-y divide-border/50">
        {rows.map(({ app, status, action }) => (
          <li key={app.build_registry_id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">{getAppDisplayName(app)}</div>
              <p className="mt-0.5 text-sm leading-6 text-muted-foreground">{projectStatusSentence(status, formatDay)}</p>
            </div>
            <div className="shrink-0">
              <ProjectBuildButton action={action} />
            </div>
          </li>
        ))}
      </ul>
      {more > 0 ? (
        <p className="mt-4 text-xs text-muted-foreground">
          {more} more {more === 1 ? 'app is' : 'apps are'} in build. {more === 1 ? 'It is' : 'They are'} in the list below.
        </p>
      ) : null}
    </Panel>
  )
}
