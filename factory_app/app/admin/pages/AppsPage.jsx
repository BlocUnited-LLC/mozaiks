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


function formatPrimaryActionLabel(action) {
  if (!action?.label) return 'Open Studio'
  return action.label === 'Open App Studio' ? 'Open Studio' : action.label
}

function getPrimaryActionPresentation(action) {
  if (action?.kind === 'build') {
    return {
      variant: 'secondary',
      className: 'font-semibold',
    }
  }

  if (action?.kind === 'overview') {
    return {
      variant: 'outline',
      className: 'border-primary/35 text-primary hover:bg-primary/10 hover:text-primary',
    }
  }

  return {
    variant: 'primary',
    className: '',
  }
}

const FILTER_OPTIONS = [
  { label: 'All', value: 'all' },
  { label: 'Needs input', value: 'needs-input' },
  { label: 'Building', value: 'building' },
  { label: 'Live', value: 'live' },
]

const SORT_OPTIONS = [
  { label: 'Needs input first', value: 'needs-input' },
  { label: 'Recently updated', value: 'recent' },
  { label: 'Name', value: 'name' },
]

function matchesFilter(row, activeFilter) {
  if (activeFilter === 'all') return true
  return row.filterBucket === activeFilter
}

function sortPortfolioRows(rows, sortValue) {
  const sorted = [...rows]
  if (sortValue === 'name') {
    return sorted.sort((left, right) => left.name.localeCompare(right.name))
  }
  if (sortValue === 'recent') {
    return sorted.sort((left, right) => right.updatedAt - left.updatedAt || left.name.localeCompare(right.name))
  }
  return sorted.sort((left, right) => (
    left.sortPriority - right.sortPriority ||
    right.updatedAt - left.updatedAt ||
    left.name.localeCompare(right.name)
  ))
}

function exportAppsCsv(rows) {
  const headers = ['app', 'status', 'state', 'updated', 'action']
  const lines = [
    headers.join(','),
    ...rows.map((row) => [
      JSON.stringify(row.name),
      JSON.stringify(row.snapshot.lifecycleLabel),
      JSON.stringify(row.stateLabel),
      JSON.stringify(row.updatedLabel),
      JSON.stringify(formatPrimaryActionLabel(row.primaryAction)),
    ].join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'workspace-apps.csv'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function AppCell({ row }) {
  return (
    <div>
      <div className="font-semibold text-foreground">{row.name}</div>
      <div className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground">{row.description}</div>
    </div>
  )
}

function AppMobileItem({ row, onOpen }) {
  const actionPresentation = getPrimaryActionPresentation(row.primaryAction)

  return (
    <article className="rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5">
      <div className="flex items-start justify-between gap-3">
        <AppCell row={row} />
        <StatusPill tone={row.snapshot.lifecycleTone}>{row.snapshot.lifecycleLabel}</StatusPill>
      </div>
      <div className="mt-4 text-sm leading-6 text-muted-foreground">
        <div>{row.stateLabel}</div>
        <div>Updated {row.updatedLabel}</div>
      </div>
      <div className="mt-4">
        <ActionButton
          onClick={() => onOpen(row)}
          size="sm"
          variant={actionPresentation.variant}
          className={actionPresentation.className}
        >
          {formatPrimaryActionLabel(row.primaryAction)}
        </ActionButton>
      </div>
    </article>
  )
}

function AppsTable({ rows, onOpen }) {
  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '38%',
      render: (row) => <AppCell row={row} />,
    },
    {
      id: 'status',
      header: 'Status',
      width: '14%',
      render: (row) => <StatusPill tone={row.snapshot.lifecycleTone}>{row.snapshot.lifecycleLabel}</StatusPill>,
    },
    {
      id: 'state',
      header: 'State',
      width: '22%',
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
      header: 'Action',
      width: '14%',
      headerClassName: 'text-right',
      cellClassName: 'text-right',
      render: (row) => {
        const actionPresentation = getPrimaryActionPresentation(row.primaryAction)

        return (
          <ActionButton
            onClick={() => onOpen(row)}
            size="sm"
            variant={actionPresentation.variant}
            className={actionPresentation.className}
          >
            {formatPrimaryActionLabel(row.primaryAction)}
          </ActionButton>
        )
      },
    },
  ]

  return (
    <ResourceList
      items={rows}
      columns={columns}
      getItemId={(row) => row.id}
      renderMobileItem={(row) => <AppMobileItem row={row} onOpen={onOpen} />}
      empty={{
        title: 'No apps match this search',
        description: 'Adjust the search term or export the current directory snapshot for review.',
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
  const { apps, loading, error, dataMode } = useWorkspaceApps('Could not load your apps.')
  const [searchValue, setSearchValue] = useState('')
  const [activeFilter, setActiveFilter] = useState('all')
  const [sortValue, setSortValue] = useState('needs-input')
  const [importOpen, setImportOpen] = useState(false)

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    const filtered = portfolio.rows.filter((row) => {
      const matchesSearch = !search || row.searchText.includes(search)
      return matchesSearch && matchesFilter(row, activeFilter)
    })
    return sortPortfolioRows(filtered, sortValue)
  }, [activeFilter, portfolio.rows, searchValue, sortValue])
  const filterOptions = useMemo(() => (
    FILTER_OPTIONS.map((filter) => ({
      ...filter,
      count: filter.value === 'all'
        ? portfolio.rows.length
        : portfolio.rows.filter((row) => matchesFilter(row, filter.value)).length,
    }))
  ), [portfolio.rows])
  const summaryItems = [
    { id: 'tracked', label: 'Total', value: formatCompactNumber(portfolio.totalApps, '0'), detail: portfolio.latestUpdatedLabel },
    { id: 'active', label: 'Live', value: formatCompactNumber(portfolio.activeCount, '0') },
    { id: 'build', label: 'In build', value: formatCompactNumber(portfolio.buildCount, '0') },
    { id: 'blocked', label: 'Needs input', value: formatCompactNumber(portfolio.blockingAlerts, '0') },
  ]

  function handleOpen(row) {
    navigate(row.primaryAction?.href || '/apps')
  }

  function handleHeaderAction(actionId) {
    if (actionId === 'create') {
      navigate('/create?new=1')
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
            sortOptions={SORT_OPTIONS}
            sortValue={sortValue}
            onSortChange={setSortValue}
            actions={dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          />
          {visibleRows.length > 0 ? (
            <AppsTable rows={visibleRows} onOpen={handleOpen} />
          ) : (
            <InlineEmptyState
              title="No apps match this search"
              description="Adjust the search term or export the current directory snapshot for review."
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
