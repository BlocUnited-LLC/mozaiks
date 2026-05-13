import { useMemo, useState } from 'react'

import {
  CollectionToolbar,
  InlineEmptyState,
  PageHeader,
  ResourceList,
  SummaryStrip,
} from '@mozaiks/chat-ui/ui'
import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ConsoleErrorState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import { formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import { getConsoleDemoBillingRecord } from './consoleDemoData.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'
import buildWorkspacePortfolio from './workspaceConsoleModel.js'

function buildBillingRows(rows, dataMode) {
  return rows.map((row) => {
    const appId = row.app?.app_id || row.app?.id || row.id
    const billing = dataMode === 'demo' ? getConsoleDemoBillingRecord(appId) : null
    const revenue = Number(billing?.total_revenue_usd || 0)
    const failedPayments = Number(billing?.failed_payments || 0)
    const billingTone = revenue > 0 ? 'success' : failedPayments > 0 ? 'warning' : 'default'
    const billingLabel = revenue > 0 ? 'Revenue active' : failedPayments > 0 ? 'Needs review' : 'Pre-revenue'

    return {
      ...row,
      revenue,
      customers: Number(billing?.active_customers || 0),
      mrr: Number(billing?.mrr_usd || 0),
      failedPayments,
      billingTone,
      billingLabel,
    }
  })
}

export default function WorkspaceBillingPage() {
  const { apps, loading, error, dataMode } = useWorkspaceApps('Billing could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const billingRows = useMemo(() => buildBillingRows(portfolio.rows, dataMode), [dataMode, portfolio.rows])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return billingRows
    return billingRows.filter((row) => row.searchText.includes(search))
  }, [billingRows, searchValue])
  const totalRevenue = billingRows.reduce((total, row) => total + row.revenue, 0)
  const summaryItems = [
    { id: 'revenue', label: 'Total Revenue', value: formatCurrencyValue(totalRevenue, 'Pending'), detail: 'Workspace billing' },
    { id: 'active', label: 'Commercial Apps', value: formatCompactNumber(billingRows.filter((row) => row.revenue > 0).length, '0'), detail: 'Apps with active revenue' },
    { id: 'preRevenue', label: 'Pre-Revenue', value: formatCompactNumber(billingRows.filter((row) => row.revenue <= 0).length, '0'), detail: 'Before payments' },
    { id: 'review', label: 'Review Required', value: formatCompactNumber(billingRows.filter((row) => row.failedPayments > 0 || ['review', 'needs_revision'].includes(row.status)).length, '0'), detail: 'Needs follow-up' },
  ]
  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '36%',
      render: (row) => (
        <div>
          <div className="font-semibold text-foreground">{row.name}</div>
          <div className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground/88">{row.description}</div>
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '16%',
      render: (row) => <StatusPill tone={row.billingTone}>{row.billingLabel}</StatusPill>,
    },
    {
      id: 'revenue',
      header: 'Revenue',
      width: '16%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCurrencyValue(row.revenue, '$0.00'),
    },
    {
      id: 'mrr',
      header: 'MRR',
      width: '16%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCurrencyValue(row.mrr, '$0.00'),
    },
    {
      id: 'customers',
      header: 'Customers',
      width: '16%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.customers, '0'),
    },
  ]

  if (loading) return <ConsoleLoadingState label="Loading billing…" />
  if (error) return <ConsoleErrorState title="Billing Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Billing"
          subtitle="Track revenue, customers, and billing follow-up by app."
        />

        <SummaryStrip items={summaryItems} />

        <Panel
          title="Billing reporting pending"
          subtitle="Live billing detail appears here as payment systems are connected."
        >
          <div className="space-y-4">
            <CollectionToolbar
              searchValue={searchValue}
              onSearchChange={setSearchValue}
              searchPlaceholder="Search billing..."
              actions={dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
            />

            {visibleRows.length > 0 ? (
              <ResourceList items={visibleRows} columns={columns} getItemId={(row) => row.id} />
            ) : (
              <InlineEmptyState
                title="No apps match this billing search"
                description="Adjust the billing search term to bring the commercial queue back into view."
              />
            )}
          </div>
        </Panel>
      </div>
    </AdminWorkspaceLayout>
  )
}
