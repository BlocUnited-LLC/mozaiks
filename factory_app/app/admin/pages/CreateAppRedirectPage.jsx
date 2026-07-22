import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'

const CREATE_APP_WORKFLOW_PATH = '/chat?workflow=ValueEngine&mode=workflow&defer_start=1&return_to=%2Fapps'


export default function CreateAppRedirectPage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate(CREATE_APP_WORKFLOW_PATH, { replace: true })
  }, [navigate])

  return <StudioLoadingState label="Opening app builder…" />
}
