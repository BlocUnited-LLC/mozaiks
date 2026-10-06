import { useRef, useState } from 'react';
import { Alert, Button, SurfaceCard } from '../../ui/primitives/index.js';

/** Optional human feedback; attribution is bound by the server UI event. */
export default function OutcomeFeedback({ onResponse }) {
  const [rating, setRating] = useState(null);
  const [helpful, setHelpful] = useState(null);
  const [outcome, setOutcome] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const answered = rating !== null || helpful !== null || outcome !== '';
  const disabled = submitting || submitted || typeof onResponse !== 'function';

  async function submit(skip) {
    if (disabled || inFlight.current || (!skip && !answered)) return;
    inFlight.current = true;
    setSubmitting(true);
    setError('');
    try {
      const accepted = await onResponse(skip
        ? { status: 'skipped' }
        : { status: 'submitted', rating, helpful, outcome: outcome || null });
      if (accepted === false) throw new Error('Response was not accepted');
      setSubmitted(true);
    } catch {
      setError('Your feedback could not be submitted. Please try again.');
    } finally {
      inFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <SurfaceCard title="How was this result?" subtitle="Optional feedback about your experience.">
      <div className="space-y-4">
        {error ? <Alert message={error} variant="warning" /> : null}
        {submitted ? <p role="status" className="text-sm text-muted-foreground">Response received.</p> : (
          <>
            <fieldset disabled={disabled} className="space-y-2">
              <legend className="text-sm font-medium text-foreground">Rating</legend>
              <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Rating out of five">
                {[1, 2, 3, 4, 5].map((value) => (
                  <button key={value} type="button" role="radio" aria-checked={rating === value}
                    aria-label={`${value} ${value === 1 ? 'star' : 'stars'}`}
                    className={`rounded-md border px-3 py-2 text-sm ${rating === value ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground'}`}
                    onClick={() => setRating(value)}>{value} <span aria-hidden="true">★</span></button>
                ))}
              </div>
            </fieldset>
            <fieldset disabled={disabled} className="space-y-2">
              <legend className="text-sm font-medium text-foreground">Was it helpful?</legend>
              <div className="flex gap-2">
                {[true, false].map((value) => (
                  <button key={String(value)} type="button" aria-pressed={helpful === value}
                    className={`rounded-md border px-3 py-2 text-sm ${helpful === value ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground'}`}
                    onClick={() => setHelpful(value)}>{value ? 'Yes' : 'No'}</button>
                ))}
              </div>
            </fieldset>
            <label className="block space-y-2 text-sm text-foreground">
              <span>Did it achieve what you needed?</span>
              <select value={outcome} disabled={disabled} onChange={(event) => setOutcome(event.target.value)}
                className="block w-full rounded-md border border-border bg-background px-3 py-2">
                <option value="">Choose an answer (optional)</option>
                <option value="succeeded">Yes</option>
                <option value="partial">Partly</option>
                <option value="failed">No</option>
                <option value="unknown">I cannot tell yet</option>
              </select>
            </label>
            <div className="flex flex-wrap gap-3">
              <Button label={submitting ? 'Submitting…' : 'Send feedback'} disabled={disabled || !answered} onClick={() => submit(false)} />
              <Button label="Skip" variant="ghost" disabled={disabled} onClick={() => submit(true)} />
            </div>
          </>
        )}
      </div>
    </SurfaceCard>
  );
}
