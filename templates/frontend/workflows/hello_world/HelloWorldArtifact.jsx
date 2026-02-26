import React from 'react';

/**
 * HelloWorldArtifact
 *
 * This is an example workflow artifact component.
 * It renders when the "hello_world" workflow produces output.
 *
 * In a real workflow:
 *   - `data` contains the structured output from your backend agents
 *   - `status` is one of: 'running' | 'complete' | 'error'
 *   - `onAction` lets the user trigger follow-up actions
 *
 * Replace this component with your own artifact renderer.
 */
export default function HelloWorldArtifact({ data, status, onAction }) {
  const message = data?.message || 'Hello from Mozaiks!';
  const isRunning = status === 'running';

  return (
    <div style={{
      padding: '32px',
      borderRadius: '16px',
      background: 'var(--color-surface, #1e293b)',
      border: '1px solid var(--color-border-subtle, #334155)',
      maxWidth: '480px',
      margin: '0 auto',
      textAlign: 'center',
    }}>
      {/* Icon */}
      <div style={{ fontSize: '48px', marginBottom: '16px' }}>👋</div>

      {/* Title */}
      <h2 style={{
        margin: '0 0 8px',
        fontSize: '22px',
        fontWeight: '700',
        color: 'var(--color-text-primary, #f1f5f9)',
      }}>
        {isRunning ? 'Working on it…' : message}
      </h2>

      {/* Subtitle */}
      <p style={{
        margin: '0 0 24px',
        fontSize: '14px',
        color: 'var(--color-text-secondary, #94a3b8)',
        lineHeight: '1.5',
      }}>
        {isRunning
          ? 'Your workflow is running. This component updates as results stream in.'
          : 'This is your Hello World artifact. Replace this file with your real output component.'}
      </p>

      {/* Action button — optional */}
      {!isRunning && onAction && (
        <button
          onClick={() => onAction({ type: 'say_hello_again' })}
          style={{
            padding: '10px 24px',
            borderRadius: '8px',
            background: 'var(--color-primary, #3b82f6)',
            color: '#fff',
            border: 'none',
            cursor: 'pointer',
            fontSize: '14px',
            fontWeight: '600',
          }}
        >
          Run again
        </button>
      )}
    </div>
  );
}
