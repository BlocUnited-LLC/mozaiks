/**
 * App health from signals Studio actually has: the latest build's validation
 * result, how many of the app's runs failed, and which connected services are
 * not set up or not working.
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

// Session status as stored by the runtime: 1 completed, 2 failed (0 is in progress).
const FINISHED_STATUSES = new Set([1, 2])

/**
 * Average duration of the runs that have ended, from `/api/admin/runs` rows.
 *
 * A run still in progress has no duration yet. Its `runtime_sec` is the time
 * since it started, so one conversation nobody closed would dominate the
 * average; it is left out.
 *
 * @returns {{ seconds: number|null, count: number }} `seconds` is null when no run has ended
 */
export function averageFinishedRunSeconds(runs) {
  const durations = (Array.isArray(runs) ? runs : [])
    .filter((run) => run && FINISHED_STATUSES.has(run.status))
    // A missing duration is not zero seconds.
    .filter((run) => run.runtime_sec !== null && run.runtime_sec !== undefined && run.runtime_sec !== '')
    .map((run) => Number(run.runtime_sec))
    .filter((seconds) => Number.isFinite(seconds) && seconds >= 0)
  if (durations.length === 0) return { seconds: null, count: 0 }
  const total = durations.reduce((sum, seconds) => sum + seconds, 0)
  return { seconds: total / durations.length, count: durations.length }
}

export function formatRunDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return null
  const value = Number(seconds)
  if (value < 9.95) return `${value.toFixed(1)}s`
  // Round once, then split, so 119.7 s reads "2m 0s" and never "1m 60s".
  const total = Math.round(value)
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes}m ${total % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/**
 * @param {object} signals
 * @param {string|null} signals.latestValidationStatus  validation_status of the newest build, or null when none exists
 * @param {number} signals.failedRuns     runs that ended with an error (`failed_chats` from /api/admin/stats)
 * @param {number} signals.trackedRuns    all recorded runs (`tracked_chats`)
 * @param {number} signals.integrationsNotSetUp  connected services with no saved credential or incomplete setup
 * @param {number} signals.integrationsNotWorking  connected services whose last check failed or whose credential expired
 */
export function buildHealthState({
  latestValidationStatus = null,
  failedRuns = 0,
  trackedRuns = 0,
  integrationsNotSetUp = 0,
  integrationsNotWorking = 0,
} = {}) {
  const failed = count(failedRuns)
  const tracked = count(trackedRuns)
  const notSetUp = count(integrationsNotSetUp)
  const notWorking = count(integrationsNotWorking)
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

  if (notWorking > 0) {
    // A connection that fails its check or has expired does not work now,
    // whatever is saved.
    severe = true
    issues.push(`${plural(notWorking, 'integration')} ${notWorking === 1 ? 'is' : 'are'} not working.`)
  }

  if (notSetUp > 0) {
    issues.push(`${plural(notSetUp, 'integration')} ${notSetUp === 1 ? 'is' : 'are'} not set up yet.`)
  }

  if (issues.length === 0) return { label: 'No issues found', tone: 'success', issues }
  return severe
    ? { label: 'Problems found', tone: 'destructive', issues }
    : { label: 'Needs attention', tone: 'warning', issues }
}

export default buildHealthState
