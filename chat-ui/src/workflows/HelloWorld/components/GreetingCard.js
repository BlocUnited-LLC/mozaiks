// ==============================================================================
// FILE: workflows/HelloWorld/components/GreetingCard.js
// DESCRIPTION: UI artifact rendered when the greet tool fires.
//
// The runtime calls this component with:
//   data   — the return value of greet() e.g. "Hello, Alice! 👋 Welcome to Mozaiks."
//   status — 'running' | 'complete' | 'error'
//
// Replace this file with your own artifact renderer once you rename
// the workflow.
// ==============================================================================

import React from 'react';

export default function GreetingCard({ data, status }) {
  const message = typeof data === 'string' ? data : data?.message || 'Hello! 👋';
  const isRunning = status === 'running';

  return (
    <div style={{
      padding: '32px',
      borderRadius: '16px',
      background: 'var(--color-surface, #0f1724)',
      border: '1px solid var(--color-border-subtle, #1a3a2a)',
      maxWidth: '480px',
      margin: '16px auto',
      textAlign: 'center',
      boxShadow: '0 4px 24px rgba(16,185,129,0.08)',
    }}>
      {/* Icon */}
      <div style={{ fontSize: '52px', marginBottom: '16px' }}>
        {isRunning ? '⏳' : '👋'}
      </div>

      {/* Greeting */}
      <h2 style={{
        margin: '0 0 8px',
        fontSize: '22px',
        fontWeight: '700',
        color: 'var(--color-text-primary, #e6eef8)',
      }}>
        {isRunning ? 'Preparing your greeting…' : message}
      </h2>

      {/* Subtitle */}
      <p style={{
        margin: 0,
        fontSize: '13px',
        color: 'var(--color-text-secondary, #94a3b8)',
        lineHeight: '1.5',
      }}>
        {isRunning
          ? 'The GreeterAgent is working.'
          : 'This is your GreetingCard artifact. Replace it with your own component.'}
      </p>
    </div>
  );
}
