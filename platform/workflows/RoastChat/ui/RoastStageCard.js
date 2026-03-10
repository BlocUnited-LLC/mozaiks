import React from 'react';

function verdictColor(verdict) {
  const value = String(verdict || '').toLowerCase();
  if (value === 'accept') return '#10b981';
  if (value === 'reject') return '#ef4444';
  return '#f59e0b';
}

export default function RoastStageCard({ payload = {}, data = {}, status }) {
  const source = payload && Object.keys(payload).length > 0 ? payload : data;
  const title = source.title || 'Roast Jury Verdict';
  const cards = Array.isArray(source.cards) ? source.cards : [];
  const verdict = source.overall_verdict || 'conditional';
  const avgScore = Number.isFinite(source.average_score) ? source.average_score : 0;
  const objections = Array.isArray(source.top_objections) ? source.top_objections : [];
  const mode = source.presentation_mode || source.mode || 'artifact';
  const isInlinePreview = mode === 'inline';
  const cardsToRender = isInlinePreview ? cards.slice(0, 2) : cards;
  const objectionsToRender = isInlinePreview ? objections.slice(0, 2) : objections;

  return (
    <div style={{
      margin: isInlinePreview ? '10px auto' : '16px auto',
      maxWidth: isInlinePreview ? '700px' : '820px',
      borderRadius: '18px',
      border: '1px solid rgba(148,163,184,0.25)',
      background: 'linear-gradient(145deg, rgba(15,23,42,0.95), rgba(30,41,59,0.95))',
      padding: isInlinePreview ? '14px' : '18px',
      color: '#e2e8f0',
      boxShadow: '0 14px 38px rgba(2,6,23,0.32)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center', marginBottom: '14px' }}>
        <h3 style={{ margin: 0, fontSize: '20px' }}>{title}</h3>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{
            display: 'inline-block',
            padding: '4px 10px',
            borderRadius: '999px',
            border: '1px solid rgba(148,163,184,0.35)',
            color: '#cbd5e1',
            fontSize: '11px',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}>
            {isInlinePreview ? 'inline preview' : 'artifact stage'}
          </span>
          <span style={{
            display: 'inline-block',
            padding: '4px 10px',
            borderRadius: '999px',
            border: `1px solid ${verdictColor(verdict)}`,
            color: verdictColor(verdict),
            fontSize: '12px',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
          }}>
            {String(verdict)}
          </span>
          <span style={{ fontSize: '12px', color: '#cbd5e1' }}>Score: {avgScore}/100</span>
        </div>
      </div>

      <div style={{ display: 'grid', gap: '10px' }}>
        {cardsToRender.map((card, index) => (
          <div key={card.task_key || index} style={{
            border: '1px solid rgba(148,163,184,0.2)',
            borderRadius: '12px',
            padding: '12px',
            background: 'rgba(15,23,42,0.45)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px', gap: '8px' }}>
              <strong>{card.judge || 'Judge'}</strong>
              <span style={{ fontSize: '12px', color: '#cbd5e1' }}>{card.verdict} · {card.score}/100</span>
            </div>
            <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}>Trait focus: {card.trait || 'n/a'}</div>
            <div style={{ fontSize: '14px', marginBottom: '4px' }}>{card.roast_line || 'No roast line generated.'}</div>
            {card.positive_spin ? (
              <div style={{ fontSize: '12px', color: '#86efac' }}>Positive spin: {card.positive_spin}</div>
            ) : null}
            {card.objection ? (
              <div style={{ fontSize: '12px', color: '#fca5a5' }}>Objection: {card.objection}</div>
            ) : null}
          </div>
        ))}
      </div>

      {objectionsToRender.length > 0 ? (
        <div style={{ marginTop: '12px' }}>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Top objections</div>
          <ul style={{ margin: 0, paddingLeft: '18px' }}>
            {objectionsToRender.map((item, idx) => <li key={idx}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {isInlinePreview ? (
        <div style={{ marginTop: '10px', fontSize: '12px', color: '#93c5fd' }}>
          Opening full artifact panel for complete jury breakdown.
        </div>
      ) : null}

      {status === 'running' ? (
        <div style={{ marginTop: '10px', fontSize: '12px', color: '#93c5fd' }}>Updating roast panel...</div>
      ) : null}
    </div>
  );
}
