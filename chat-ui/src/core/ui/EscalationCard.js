import { useState } from 'react';
import { Button, SurfaceCard } from '../../ui/primitives/index.js';

/**
 * EscalationCard — inline chat card rendered when the AI detects user frustration.
 *
 * Injected into the message stream (tool_name: 'EscalationCard') when the AI
 * response contains escalation cues per the ai.json support.escalation_prompt.
 *
 * onResponse({ action: 'open_support' }) — user wants an operator
 * onResponse({ action: 'dismiss' })       — user declines, stays in chat
 *
 * The host's onAgentAction intercepts 'open_support', creates a deterministic
 * support request when possible, and navigates to the profile support tab.
 */
export default function EscalationCard({ payload = {}, onResponse }) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const title = payload.title || 'Need help from a person?';
  const message =
    payload.message ||
    'An operator can follow up with you directly. Tap below to send a support request.';

  const handleEscalate = async () => {
    try {
      if (onResponse) await onResponse({ action: 'open_support' });
    } finally {
      setDismissed(true);
    }
  };

  const handleDismiss = () => {
    if (onResponse) onResponse({ action: 'dismiss' });
    setDismissed(true);
  };

  return (
    <SurfaceCard title={title}>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground leading-relaxed">{message}</p>
        <div className="flex flex-wrap gap-3">
          <Button
            label="Talk to an operator"
            variant="primary"
            onClick={handleEscalate}
          />
          <Button
            label="I'm okay, thanks"
            variant="ghost"
            onClick={handleDismiss}
          />
        </div>
      </div>
    </SurfaceCard>
  );
}
