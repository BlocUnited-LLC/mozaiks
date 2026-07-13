import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'


export default function CreateAppRedirectPage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/chat?workflow=ValueEngine&mode=workflow&defer_start=1', { replace: true })
  }, [navigate])

  return <StudioLoadingState label="Opening app builder…" />
}
