import { useWorkflowStart } from '@mozaiks/chat-ui/hooks/useWorkflowStart.js'

import { ActionButton, LinkButton } from '../../ui/components/StudioShared.jsx'

/**
 * The one build button of the Project page and of My Apps' builds in progress.
 *
 * `action` comes from `projectAction()`. A `start` action asks the server to
 * start the workflow bound to the app's registered build, the same request
 * the build review page sends; the server checks ownership and opens the chat.
 *
 * Styled like My Apps' "Continue Build" (ActionButton, secondary, semibold),
 * so the same action looks the same wherever it appears.
 */
export default function ProjectBuildButton({ action, size = 'sm', className = '' }) {
  const buttonClass = ['font-semibold', className].filter(Boolean).join(' ')
  const { startWorkflow, starting, error } = useWorkflowStart()
  if (!action) return null
  if (action.type === 'link') {
    return <LinkButton to={action.href} variant="secondary" size={size} className={buttonClass}>{action.label}</LinkButton>
  }
  if (action.type === 'start') {
    return (
      <span className="inline-flex flex-col items-start gap-1">
        <ActionButton
          variant="secondary"
          size={size}
          className={buttonClass}
          disabled={starting}
          onClick={() => startWorkflow(action.workflowId, {}, {
            trigger_source: 'manual',
            build_registry_id: action.buildRegistryId,
          })}
        >
          {starting ? 'Opening…' : action.label}
        </ActionButton>
        {error ? <span role="alert" className="text-xs text-destructive">{error}</span> : null}
      </span>
    )
  }
  return action.reason ? <span className="text-sm text-muted-foreground">{action.reason}</span> : null
}
