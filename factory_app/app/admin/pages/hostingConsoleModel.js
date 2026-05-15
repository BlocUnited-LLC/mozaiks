function buildDefaultDomain(appId) {
  if (!appId) return 'app.mozaiks.app'
  return `${String(appId).replace(/[^a-z0-9-]/gi, '-').toLowerCase()}.mozaiks.app`
}

function buildEnvironmentLabel(status, explicitEnvironment = null) {
  if (explicitEnvironment) return explicitEnvironment
  if (status === 'active') return 'Production'
  if (status === 'deploying') return 'Provisioning'
  if (status === 'review' || status === 'configuring') return 'Staging'
  return 'Build'
}

export function formatResourceValue(value, unit = '') {
  if (value == null || Number.isNaN(Number(value))) return 'Pending'
  return `${Number(value)}${unit}`
}

export function buildHostingSections({ appId, appName, status, deploymentRecord = null }) {
  const domains = Array.isArray(deploymentRecord?.domains) && deploymentRecord.domains.length > 0
    ? deploymentRecord.domains
    : []
  const fallbackDomain = domains[0] || buildDefaultDomain(appId || appName)
  const domainItems = domains.length > 0
    ? domains.map((domain, index) => ({
        label: domain,
        status: index === 0 ? 'Primary' : 'Alias',
        detail: index === 0 ? 'Primary app domain served by managed hosting.' : 'Additional mapped domain for this app.',
      }))
    : []
  const emailItems = domains.length > 0
    ? [
        {
          label: `support@${fallbackDomain.replace(/^api\./, '')}`,
          status: 'Shared inbox',
          detail: 'Customer-facing inbox tied to the primary domain.',
        },
        {
          label: `ops@${fallbackDomain.replace(/^api\./, '')}`,
          status: 'Forwarding',
          detail: 'Operator mailbox for deployment and hosting notices.',
        },
      ]
    : []
  const dnsItems = domains.length > 0
    ? domains.map((domain) => ({
        label: domain,
        status: 'Managed TLS',
        detail: 'DNS, TLS, and renewal are handled by the managed hosting layer.',
      }))
    : []
  const backupItems = [
    {
      label: 'Daily backup',
      status: deploymentRecord?.failed ? 'Needs review' : 'Healthy',
      detail: `${formatResourceValue(deploymentRecord?.storage_gb, ' GB')} of storage mirrored on a daily schedule.`,
    },
    {
      label: 'Bandwidth posture',
      status: deploymentRecord?.bandwidth_gb != null ? 'Tracked' : 'Pending',
      detail: `${formatResourceValue(deploymentRecord?.bandwidth_gb, ' GB')} served through the current environment.`,
    },
  ]

  return {
    providerLabel: 'Managed provider',
    planLabel: status === 'active' ? 'Business Hosting' : status === 'deploying' ? 'Provisioning Plan' : 'Managed Build Hosting',
    environmentLabel: buildEnvironmentLabel(status, deploymentRecord?.environment),
    uptimePercent: deploymentRecord?.uptime_percent ?? null,
    storageGb: deploymentRecord?.storage_gb ?? null,
    bandwidthGb: deploymentRecord?.bandwidth_gb ?? null,
    domainItems,
    emailItems,
    dnsItems,
    backupItems,
  }
}

export default buildHostingSections