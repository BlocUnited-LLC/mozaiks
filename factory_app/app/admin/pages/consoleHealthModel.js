import { getRuntimeReadinessLabel, normalizeAppStatus } from './appConsoleModel.js'

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

const STATUS_BASE_SCORE = {
  draft: 54,
  building: 60,
  review: 68,
  configuring: 72,
  deploying: 76,
  active: 88,
  needs_revision: 42,
  archived: 50,
}

export function formatPercentValue(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback
  const numeric = Number(value)
  return `${numeric >= 99 ? numeric.toFixed(2) : numeric.toFixed(1)}%`
}

export function buildHealthState({
  status,
  totalErrors = 0,
  runtimeReadiness = null,
  latestValidationStatus = null,
  uptimePercent = null,
  hasDeploymentFailure = false,
  missingSecrets = 0,
}) {
  const normalized = normalizeAppStatus(status)
  let score = STATUS_BASE_SCORE[normalized] ?? 60
  const issues = []

  if (runtimeReadiness && runtimeReadiness !== 'entry_point_configured') {
    score -= 12
    issues.push(getRuntimeReadinessLabel(runtimeReadiness))
  }

  if (hasDeploymentFailure) {
    score -= 18
    issues.push('Hosting reports a failed rollout signal')
  }

  if (latestValidationStatus === 'failed') {
    score -= 16
    issues.push('Latest build validation failed')
  } else if (latestValidationStatus && latestValidationStatus !== 'passed') {
    score -= 6
    issues.push('Latest build validation is still pending')
  }

  if (Number(totalErrors || 0) > 0) {
    score -= Math.min(Number(totalErrors || 0) * 3, 18)
    issues.push(`${Number(totalErrors || 0)} recent workflow errors recorded`)
  }

  if (Number(missingSecrets || 0) > 0) {
    score -= Math.min(Number(missingSecrets || 0) * 4, 12)
    issues.push(`${Number(missingSecrets || 0)} integrations still need secrets`)
  }

  if (typeof uptimePercent === 'number') {
    if (uptimePercent < 99) {
      score -= 12
      issues.push(`Uptime is ${formatPercentValue(uptimePercent)}`)
    } else if (uptimePercent < 99.9) {
      score -= 6
      issues.push(`Uptime is ${formatPercentValue(uptimePercent)}`)
    }
  }

  score = clamp(Math.round(score), 0, 100)

  if (score >= 85) {
    return {
      score,
      label: 'Healthy',
      tone: 'success',
      issues: issues.slice(0, 3),
      runtimeLabel: getRuntimeReadinessLabel(runtimeReadiness),
      uptimeLabel: formatPercentValue(uptimePercent),
    }
  }

  if (score >= 70) {
    return {
      score,
      label: 'Stable',
      tone: 'primary',
      issues: issues.slice(0, 3),
      runtimeLabel: getRuntimeReadinessLabel(runtimeReadiness),
      uptimeLabel: formatPercentValue(uptimePercent),
    }
  }

  if (score >= 55) {
    return {
      score,
      label: 'Needs Attention',
      tone: 'warning',
      issues: issues.slice(0, 3),
      runtimeLabel: getRuntimeReadinessLabel(runtimeReadiness),
      uptimeLabel: formatPercentValue(uptimePercent),
    }
  }

  return {
    score,
    label: 'At Risk',
    tone: 'destructive',
    issues: issues.slice(0, 3),
    runtimeLabel: getRuntimeReadinessLabel(runtimeReadiness),
    uptimeLabel: formatPercentValue(uptimePercent),
  }
}

export default buildHealthState