/**
 * App health from signals Studio actually has: the latest build's validation
 * result, how many of the app's runs failed, and which connected services still
 * lack a credential.
 *
 * There is no numeric score. A score needs a baseline, and the only baseline
 * available was a fixed number per lifecycle stage; that reads as a
 * measurement and is not one. Uptime and hosting are absent for the same
 * reason: this Studio has no source for them.
 */

function count(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0
}

function plural(number, singular, pluralForm = `${singular}s`) {
  return `${number} ${number === 1 ? singular : pluralForm}`
}

export function validationLabel(status) {
  if (status === 'passed') return 'Passed'
  if (status === 'failed') return 'Failed'
  if (!status) return 'No build yet'
  return 'Not passed yet'
}

export function validationTone(status) {
  if (status === 'passed') return 'success'
  if (status === 'failed') return 'destructive'
  return 'warning'
}

/**
 * @param {object} signals
 * @param {string|null} signals.latestValidationStatus  validation_status of the newest build, or null when none exists
 * @param {number} signals.failedRuns     runs that ended with an error (`failed_chats` from /api/admin/stats)
 * @param {number} signals.trackedRuns    all recorded runs (`tracked_chats`)
 * @param {number} signals.integrationIssues  connected services without a saved credential
 */
export function buildHealthState({
  latestValidationStatus = null,
  failedRuns = 0,
  trackedRuns = 0,
  integrationIssues = 0,
} = {}) {
  const failed = count(failedRuns)
  const tracked = count(trackedRuns)
  const gaps = count(integrationIssues)
  const issues = []
  let severe = false

  if (latestValidationStatus === 'failed') {
    severe = true
    issues.push('The latest build failed validation.')
  } else if (latestValidationStatus && latestValidationStatus !== 'passed') {
    issues.push('The latest build has not passed validation yet.')
  }

  if (failed > 0) {
    severe = true
    issues.push(
      tracked > 0
        ? `${failed} of ${plural(tracked, 'run')} ended with an error.`
        : `${plural(failed, 'run')} ended with an error.`,
    )
  }

  if (gaps > 0) {
    issues.push(`${plural(gaps, 'integration')} still ${gaps === 1 ? 'needs' : 'need'} a credential.`)
  }

  if (issues.length === 0) return { label: 'No issues found', tone: 'success', issues }
  return severe
    ? { label: 'Problems found', tone: 'destructive', issues }
    : { label: 'Needs attention', tone: 'warning', issues }
}

export default buildHealthState
