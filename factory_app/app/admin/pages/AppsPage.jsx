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

function ImportAppOverlay({ open, onClose, onImport }) {
  return (
    <StudioSlideOver
      open={open}
      title="Import App"
      description="Register an existing application so Mozaiks can route discovery and refinement from Studio."
      onClose={onClose}
    >
      <Form
        id="import-app"
        fields={[
          {
            name: 'url',
            label: 'Existing app URL',
            type: 'text',
            required: true,
            placeholder: 'https://example.com',
          },
          {
            name: 'notes',
            label: 'Notes for discovery',
            type: 'textarea',
            placeholder: 'Current stack, product purpose, and constraints to preserve.',
          },
        ]}
        submit_label="Continue Intake"
        cancel_label="Cancel"
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
      navigate('/chat?workflow=ValueEngine&mode=workflow&defer_start=1')
    } else if (actionId === 'import') {
      setImportOpen(true)
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
          ) : (
            <InlineEmptyState
              title="No apps match this search"
              description="Adjust the search term or clear the filter."
            />
          )}
        </section>

        <ImportAppOverlay
          open={importOpen}
          onClose={() => setImportOpen(false)}
          onImport={() => {
            setImportOpen(false)
            navigate('/apps/new')
          }}
        />
      </div>
    </WorkspaceLayout>
  )
}
