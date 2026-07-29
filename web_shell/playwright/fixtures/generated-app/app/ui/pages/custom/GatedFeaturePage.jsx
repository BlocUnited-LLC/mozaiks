/**
 * GatedFeaturePage — fixture page for Playwright entitlement-gate tests.
 *
 * Simulates a generated custom route page that calls a plan-gated module
 * action. Demonstrates the correct pattern:
 *   - Use isEntitlementRequiredError(err) to detect ENTITLEMENT_REQUIRED
 *   - Navigate to entitlementUpgradePath(err) on denial
 *   - Do not retry the action automatically
 *
 * This page is registered in the fixture app's ui/index.js and is used
 * by web_shell/playwright/generated-ui/entitlement-upgrade.spec.js.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  isEntitlementRequiredError,
  entitlementUpgradePath,
  moduleAction,
} from '../../lib/moduleApi.js'

export default function GatedFeaturePage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('idle')  // idle | loading | success | error
  const [errorMsg, setErrorMsg] = useState(null)

  async function callGatedAction() {
    setStatus('loading')
    setErrorMsg(null)
    try {
      await moduleAction('premium_reports', 'generate_report', { period: 'monthly' })
      setStatus('success')
    } catch (err) {
      if (isEntitlementRequiredError(err)) {
        navigate(entitlementUpgradePath(err))
        return
      }
      setStatus('error')
      setErrorMsg(err.message || 'Unexpected error')
    }
  }

  useEffect(() => {
    // Auto-trigger on mount so Playwright tests don't need to click
    if (status === 'idle') {
      callGatedAction()
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  if (status === 'loading') {
    return <div data-testid="gated-feature-loading">Loading...</div>
  }
  if (status === 'success') {
    return <div data-testid="gated-feature-success">Report generated.</div>
  }
  if (status === 'error') {
    return <div data-testid="gated-feature-error">{errorMsg}</div>
  }
  return null
}
