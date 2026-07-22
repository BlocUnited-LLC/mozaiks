import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'

const CREATE_APP_PATH = '/create'


export default function CreateAppRedirectPage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate(CREATE_APP_PATH, { replace: true })
  }, [navigate])

  return <StudioLoadingState label="Opening app builder…" />
}
