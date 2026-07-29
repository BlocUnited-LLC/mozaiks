/**
 * AppsPage — curated workspace app directory.
 */

import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  CollectionToolbar,
  Form,
  InlineEmptyState,
  ResourceList,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  StudioErrorState,
  StudioLoadingState,
  StudioSlideOver,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber } from './AppStudioChrome.jsx'
import { API_BASE } from './studioApi.js'
import buildWorkspacePortfolio from './workspaceStudioModel.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'

const FILTER_OPTIONS = [
  { label: 'All', value: 'all' },
  { label: 'Needs input', value: 'needs-input' },
  { label: 'Building', value: 'building' },
  { label: 'Live', value: 'live' },
]

const CREATE_APP_PATH = '/create'

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

function AppCell({ row }) {
  return (
    <div>
      <div className="font-semibold text-foreground">{row.name}</div>
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

const DashboardIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
  </svg>
)

function AppMobileItem({ row, onOpen, onDashboard, onDelete }) {
  return (
    <article
      className="rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5 cursor-pointer hover:bg-card/50 transition-colors"
      onClick={() => onOpen(row)}
    >
      <div className="flex items-start justify-between gap-3">
        <AppCell row={row} />
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
        <ActionButton
          onClick={(e) => { e.stopPropagation(); onDelete(row) }}
          size="sm"
          variant="ghost"
          className="text-destructive hover:bg-destructive/10 px-2"
          aria-label="Delete app"
        >
          <TrashIcon />
        </ActionButton>
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
          <ActionButton
            onClick={(e) => { e.stopPropagation(); onDelete(row) }}
            size="sm"
            variant="ghost"
            className="text-destructive hover:bg-destructive/10 px-2"
            aria-label="Delete app"
          >
            <TrashIcon />
          </ActionButton>
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

function ImportAppOverlay({ open, onClose, onImport, error, busy }) {
  return (
    <StudioSlideOver
      open={open}
      title="Import App"
      description="Clone an existing repository and build App Intelligence before agents edit it."
      onClose={onClose}
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-destructive/35 bg-destructive/8 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      <Form
        id="import-app"
        fields={[
          {
            name: 'name',
            label: 'App name',
            type: 'text',
            placeholder: 'Mozaiks App',
          },
          {
            name: 'repo_url',
            label: 'Repository URL',
            type: 'text',
            required: true,
            placeholder: 'https://github.com/org/repo',
          },
          {
            name: 'branch',
            label: 'Branch',
            type: 'text',
            placeholder: 'main',
          },
          {
            name: 'monorepo_path',
            label: 'Monorepo path',
            type: 'text',
            placeholder: 'apps/web',
          },
          {
            name: 'ignored_paths',
            label: 'Ignored paths',
            type: 'textarea',
            placeholder: 'One path per line, such as docs/archive or examples/large-demo.',
          },
          {
            name: 'notes',
            label: 'Notes',
            type: 'textarea',
            placeholder: 'Current stack, product purpose, and constraints to preserve.',
          },
        ]}
        submit_label="Import Repository"
        cancel_label="Cancel"
        disabled={busy}
        onCancel={onClose}
        onSubmit={onImport}
      />
    </StudioSlideOver>
  )
}

export default function AppsPage() {
  const navigate = useNavigate()
  const { apps, loading, error, deleteApp } = useWorkspaceApps('Could not load your apps.')
  const [searchValue, setSearchValue] = useState('')
  const [activeFilter, setActiveFilter] = useState('all')
  const [importOpen, setImportOpen] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState(null)

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])

  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    const filtered = portfolio.rows.filter((row) => {
      const matchesSearch = !search || row.searchText.includes(search)
      return matchesSearch && matchesFilter(row, activeFilter)
    })
    return sortByNeedsInput(filtered)
  }, [activeFilter, portfolio.rows, searchValue])

  const filterOptions = useMemo(() => (
    FILTER_OPTIONS.map((filter) => ({
      ...filter,
      count: filter.value === 'all'
        ? portfolio.rows.length
        : portfolio.rows.filter((row) => matchesFilter(row, filter.value)).length,
    }))
  ), [portfolio.rows])

  const summaryItems = [
    { id: 'tracked', label: 'Total', value: formatCompactNumber(portfolio.totalApps, '0') },
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

  function handleHeaderAction(actionId) {
    if (actionId === 'create') {
      navigate(CREATE_APP_PATH)
    } else if (actionId === 'import') {
      setImportError(null)
      setImportOpen(true)
    }
  }

  async function handleImport(values) {
    setImportBusy(true)
    setImportError(null)
    try {
      const repoUrl = String(values.repo_url || '').trim()
      const name = String(values.name || '').trim() || repoUrl.split('/').filter(Boolean).pop()?.replace(/\.git$/, '') || 'Imported app'
      const ignoredPaths = String(values.ignored_paths || '')
        .split(/\r?\n|,/)
        .map((item) => item.trim())
        .filter(Boolean)
      const createRes = await fetch(`${API_BASE}/api/studio/apps`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description: String(values.notes || '').trim() || `Imported from ${repoUrl}`,
          status: 'building',
          name_source: 'imported_app',
          build_context_profile: {
            source: 'repository_import',
            repo_url: repoUrl,
            branch: String(values.branch || '').trim() || null,
            monorepo_path: String(values.monorepo_path || '').trim() || null,
            ignored_paths: ignoredPaths,
          },
        }),
      })
      if (!createRes.ok) throw new Error('App record could not be created.')
      const createPayload = await createRes.json()
      const appId = createPayload?.app?.app_id
      if (!appId) throw new Error('Created app record did not return an app id.')

      const importRes = await fetch(`${API_BASE}/api/studio/apps/${encodeURIComponent(appId)}/context/source-import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_kind: 'git_repository',
          repo_url: repoUrl,
          branch: String(values.branch || '').trim() || null,
          monorepo_path: String(values.monorepo_path || '').trim() || null,
          ignored_paths: ignoredPaths,
          make_current: true,
        }),
      })
      if (!importRes.ok) throw new Error('Source import job could not be started.')
      setImportOpen(false)
      navigate(`/apps/${encodeURIComponent(appId)}/overview`)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Repository import could not be started.')
    } finally {
      setImportBusy(false)
    }
  }

  if (loading) return <StudioLoadingState label="Loading your apps…" />
  if (error) return <StudioErrorState title="Could not load apps" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Apps"
          subtitle="Manage your apps, continue builds, and open app Studio."
          actions={[
            { id: 'create', label: 'Create App' },
            { id: 'import', label: 'Import App', variant: 'outline' },
          ]}
          onAction={handleHeaderAction}
          summaryItems={summaryItems}
        />

        <section className="space-y-4">
          <CollectionToolbar
            searchValue={searchValue}
            onSearchChange={setSearchValue}
            searchPlaceholder="Search apps..."
            filters={filterOptions}
            activeFilter={activeFilter}
            onFilterChange={setActiveFilter}
            actions={null}
          />
          {visibleRows.length > 0 ? (
            <AppsTable rows={visibleRows} onOpen={handleOpen} onDashboard={handleDashboard} onDelete={handleDelete} />
          ) : portfolio.rows.length === 0 ? (
            <InlineEmptyState
              title="No apps yet"
              description="Hit Create App above to get started. Describe what you want to build and Mozaiks handles the scaffold."
            />
          ) : (
            <InlineEmptyState
              title="No apps match this search"
              description="Adjust the search term or clear the filter."
            />
          )}
        </section>

        <ImportAppOverlay
          open={importOpen}
          onClose={() => {
            if (importBusy) return
            setImportOpen(false)
            setImportError(null)
          }}
          onImport={handleImport}
          error={importError}
          busy={importBusy}
        />
      </div>
    </WorkspaceLayout>
  )
}
