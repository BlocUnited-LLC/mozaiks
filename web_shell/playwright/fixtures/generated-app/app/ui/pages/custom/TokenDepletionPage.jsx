import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  insufficientTokensRecoveryPath,
  isInsufficientTokensError,
  moduleAction,
} from '../../lib/moduleApi.js'

export default function TokenDepletionPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('idle')

  async function generate() {
    setStatus('loading')
    try {
      await moduleAction('premium_reports', 'generate_report', { period: 'monthly' })
      setStatus('success')
    } catch (err) {
      if (isInsufficientTokensError(err)) {
        const recoveryPath = insufficientTokensRecoveryPath(err)
        if (recoveryPath) {
          navigate(recoveryPath)
        } else {
          setStatus('contact')
        }
        return
      }
      setStatus('error')
    }
  }

  return (
    <main>
      {status === 'contact' && (
        <p role="alert">AI capacity is depleted. Contact an administrator for help.</p>
      )}
      {status === 'error' && <p role="alert">The report could not be generated.</p>}
      {status === 'success' && <p>Report generated.</p>}
      <button disabled={status === 'loading' || status === 'contact'} onClick={generate}>
        Generate report
      </button>
    </main>
  )
}
