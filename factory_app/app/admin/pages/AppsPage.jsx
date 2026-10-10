/**
 * AppsPage — curated workspace app directory.
 */

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  CollectionToolbar,
  InlineEmptyState,
  ResourceList,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  StudioErrorState,
  StudioLoadingState,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber } from './AppStudioChrome.jsx'
import {
  fetchDashboardConfig,
  getDefaultPortalRoute,
} from './dashboardRoutes.js'
import buildWorkspacePortfolio from './workspaceStudioModel.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'

const FILTER_OPTIONS = [
  { label: 'All', value: 'all' },
  { label: 'Needs input', value: 'needs-input' },
  { label: 'Building', value: 'building' },
  { label: 'Live', value: 'live' },
]
function matchesFilter(row, activeFilter) {
  if (activeFilter === 'all') return true
  return row.filterBucket === activeFilter
}

function sortByNeedsInput(rows) {
  return [...rows].sort((left, right) => (
    left.sortPriority - right.sortPriority ||
    right.updatedAt - left.updatedAt ||
    left.name.localeCompare(right.name)
  ))
}

function AppCell({ row, onOpen }) {
  return (
    <div>
      {onOpen ? (
        <button
          type="button"
          onClick={() => onOpen(row)}
          aria-label={`Open ${row.name}`}
          className="text-left font-semibold text-foreground underline-offset-4 hover:underline focus-visible:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          <span>{row.name}</span>
          <span aria-hidden="true"> ↗</span>
        </button>
      ) : <div className="font-semibold text-foreground">{row.name}</div>}
      <div className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground">{row.description}</div>
    </div>
  )
}

const TrashIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
    <path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
  </svg>
)

function AppMobileItem({ row, onOpen, onDashboard, onDelete }) {
  return (
    <article
      className="rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5"
    >
      <div className="flex items-start justify-between gap-3">
        <AppCell row={row} onOpen={onOpen} />
        <StatusPill tone={row.snapshot.lifecycleTone}>{row.snapshot.lifecycleLabel}</StatusPill>
      </div>
      <div className="mt-3 text-sm leading-6 text-muted-foreground">
        <div>{row.stateLabel}</div>
        <div>Updated {row.updatedLabel}</div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        {row.primaryAction?.kind === 'build' && (
          <ActionButton
            onClick={(e) => { e.stopPropagation(); onOpen(row) }}
            size="sm"
            variant="secondary"
            className="font-semibold"
          >
            Continue Build
          </ActionButton>
        )}
        {row.dashboardHref && (
          <ActionButton
            onClick={(e) => { e.stopPropagation(); onDashboard(row) }}
            size="sm"
            variant="outline"
            className="border-primary/35 text-primary hover:bg-primary/10 hover:text-primary"
          >
            Dashboard
          </ActionButton>
        )}
        {onDelete && <ActionButton
          onClick={(e) => { e.stopPropagation(); onDelete(row) }}
          size="sm"
          variant="ghost"
          className="text-destructive hover:bg-destructive/10 px-2"
          aria-label="Delete app"
        >
          <TrashIcon />
        </ActionButton>}
      </div>
    </article>
  )
}

function AppsTable({ rows, onOpen, onDashboard, onDelete }) {
  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '34%',
      render: (row) => <AppCell row={row} />,
    },
    {
      id: 'status',
      header: 'Status',
      width: '13%',
      render: (row) => <StatusPill tone={row.snapshot.lifecycleTone}>{row.snapshot.lifecycleLabel}</StatusPill>,
    },
    {
      id: 'state',
      header: 'State',
      width: '19%',
      cellClassName: 'text-muted-foreground',
      render: (row) => row.stateLabel,
    },
    {
      id: 'updated',
      header: 'Updated',
      width: '12%',
      cellClassName: 'text-muted-foreground',
      render: (row) => row.updatedLabel,
    },
    {
      id: 'action',
      header: '',
      width: '22%',
      headerClassName: 'text-right',
      cellClassName: 'text-right',
      render: (row) => (
        <span className="inline-flex items-center gap-1.5 justify-end">
          {row.primaryAction?.kind === 'build' && (
            <ActionButton
              onClick={(e) => { e.stopPropagation(); onOpen(row) }}
              size="sm"
              variant="secondary"
              className="font-semibold"
            >
              Continue Build
            </ActionButton>
          )}
          {row.dashboardHref && (
            <ActionButton
              onClick={(e) => { e.stopPropagation(); onDashboard(row) }}
              size="sm"
              variant="outline"
              className="border-primary/35 text-primary hover:bg-primary/10 hover:text-primary"
            >
              Dashboard
            </ActionButton>
          )}
          {onDelete && <ActionButton
            onClick={(e) => { e.stopPropagation(); onDelete(row) }}
            size="sm"
            variant="ghost"
            className="text-destructive hover:bg-destructive/10 px-2"
            aria-label="Delete app"
          >
            <TrashIcon />
          </ActionButton>}
        </span>
      ),
    },
  ]

  return (
    <ResourceList
      items={rows}
      columns={columns}
      getItemId={(row) => row.id}
      onRowClick={onOpen}
      renderMobileItem={(row) => <AppMobileItem row={row} onOpen={onOpen} onDashboard={onDashboard} onDelete={onDelete} />}
      empty={{
        title: 'No apps match this search',
        description: 'Adjust the search term or clear the filter.',
      }}
    />
  )
}

export function AppsDirectory({
  apps, loading, error, deleteApp, navigationForApp, pagination,
  searchValue: controlledSearch, onSearchChange, activeFilter: controlledFilter, onFilterChange,
}) {
  const navigate = useNavigate()
  const [dashboardConfig, setDashboardConfig] = useState(null)
  const [localSearch, setLocalSearch] = useState('')
  const [localFilter, setLocalFilter] = useState('all')
  const searchValue = controlledSearch ?? localSearch
  const activeFilter = controlledFilter ?? localFilter

  useEffect(() => {
    const controller = new AbortController()
    fetchDashboardConfig({ signal: controller.signal })
      .then((payload) => setDashboardConfig(payload))
      .catch((err) => {
        if (err?.name !== 'AbortError') {
          console.error('[AppsPage] dashboard manifest load failed:', err)
        }
      })
    return () => controller.abort()
  }, [])

  const appDashboardRoute = useMemo(
    () => getDefaultPortalRoute(dashboardConfig, 'app'),
    [dashboardConfig],
  )

  const portfolio = useMemo(() => {
    const model = buildWorkspacePortfolio(apps, { appDashboardRoute })
    if (!navigationForApp) return model
    return {
      ...model,
      rows: model.rows.map((row) => {
        const { primaryAction, dashboardHref } = navigationForApp(row.app)
        return { ...row, primaryAction, dashboardHref }
      }),
    }
  }, [appDashboardRoute, apps, navigationForApp])

  const visibleRows = useMemo(() => {
    if (pagination) {
      const rowsByApp = new Map(portfolio.rows.map(row => [row.app, row]))
      return (apps || []).map(app => rowsByApp.get(app))
    }
    const search = searchValue.trim().toLowerCase()
    const filtered = portfolio.rows.filter((row) => {
      const matchesSearch = !search || row.searchText.includes(search)
      return matchesSearch && matchesFilter(row, activeFilter)
    })
    return sortByNeedsInput(filtered)
  }, [activeFilter, apps, pagination, portfolio.rows, searchValue])

  const filterOptions = useMemo(() => (
    FILTER_OPTIONS.map((filter) => ({
      ...filter,
      count: pagination ? undefined : filter.value === 'all'
        ? portfolio.rows.length
        : portfolio.rows.filter((row) => matchesFilter(row, filter.value)).length,
    }))
  ), [pagination, portfolio.rows])

  const summaryItems = [
    { id: 'tracked', label: pagination ? 'On this page' : 'Total', value: formatCompactNumber(portfolio.totalApps, '0') },
    { id: 'active', label: 'Live', value: formatCompactNumber(portfolio.activeCount, '0') },
    { id: 'build', label: 'In build', value: formatCompactNumber(portfolio.buildCount, '0') },
    { id: 'blocked', label: 'Needs input', value: formatCompactNumber(portfolio.blockingAlerts, '0') },
  ]

  function handleOpen(row) {
    navigate(row.primaryAction?.href || '/apps')
  }

  function handleDashboard(row) {
    navigate(row.dashboardHref)
  }

  async function handleDelete(row) {
    const buildRegistryId = row.id
    if (!buildRegistryId) return
    if (!window.confirm(`Remove "${row.name}" from your workspace?`)) return
    await deleteApp(buildRegistryId)
  }

  if (loading && !pagination) return <StudioLoadingState label="Loading your apps…" />
  if (error && !pagination) return <StudioErrorState title="Could not load apps" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Apps"
          subtitle={pagination ? 'Manage your apps. Search and filters include all your apps.' : 'Manage your apps, continue builds, and open app Studio.'}
          summaryItems={pagination ? summaryItems.slice(0, 1) : summaryItems}
          summaryClassName="hidden md:block"
        />

        <section className="space-y-4">
          {pagination && (
            <p className="px-1 text-xs text-muted-foreground md:hidden" aria-live="polite">
              On this page: <span className="font-semibold text-foreground">{summaryItems[0].value}</span>
            </p>
          )}
          <CollectionToolbar
            searchValue={searchValue}
            onSearchChange={onSearchChange ?? setLocalSearch}
            searchPlaceholder={pagination ? 'Search app names and descriptions...' : 'Search apps...'}
            filters={filterOptions}
            activeFilter={activeFilter}
            onFilterChange={onFilterChange ?? setLocalFilter}
          />
          {loading ? <StudioLoadingState label="Loading your apps…" /> : error ? (
            <div className="space-y-3">
              <StudioErrorState title="Could not load apps" message={error} />
              {pagination?.onRetry && <ActionButton onClick={pagination.onRetry}>Try again</ActionButton>}
            </div>
          ) : visibleRows.length > 0 ? (
            <AppsTable rows={visibleRows} onOpen={handleOpen} onDashboard={handleDashboard} onDelete={deleteApp ? handleDelete : null} />
          ) : portfolio.rows.length === 0 && !searchValue && activeFilter === 'all' && (!pagination || pagination.page === 1) ? (
            <InlineEmptyState
              title="No apps yet"
              description="Apps you create or manage will appear here."
            />
          ) : (
            <InlineEmptyState
              title={pagination && !searchValue && activeFilter === 'all' ? 'No apps on this page' : 'No apps match this search'}
              description={pagination && !searchValue && activeFilter === 'all' ? 'Go to the previous page to continue browsing.' : 'Adjust the search term or clear the filter.'}
            />
          )}
          {pagination && (
            <nav aria-label="Apps pagination" className="flex items-center justify-between gap-3">
              <span className="text-sm text-muted-foreground" aria-live="polite">Page {pagination.page}</span>
              <div className="flex gap-2">
                <ActionButton variant="outline" onClick={pagination.onPrevious} disabled={loading || !pagination.hasPrevious}>Previous</ActionButton>
                <ActionButton variant="outline" onClick={pagination.onNext} disabled={loading || Boolean(error) || !pagination.hasNext}>Next</ActionButton>
              </div>
            </nav>
          )}
        </section>

      </div>
    </WorkspaceLayout>
  )
}

export default function AppsPage() {
  const directory = useWorkspaceApps('Could not load your apps.')
  return <AppsDirectory {...directory} />
}
