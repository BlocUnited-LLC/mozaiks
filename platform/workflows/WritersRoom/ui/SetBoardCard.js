import React from 'react';

function laneTone(lane) {
  const value = String(lane || '').toLowerCase();
  if (value.includes('roast')) return { accent: '#f97316', glow: 'rgba(249,115,22,0.24)' };
  if (value.includes('observational')) return { accent: '#22c55e', glow: 'rgba(34,197,94,0.22)' };
  if (value.includes('absurd')) return { accent: '#a855f7', glow: 'rgba(168,85,247,0.22)' };
  return { accent: '#38bdf8', glow: 'rgba(56,189,248,0.22)' };
}

export default function SetBoardCard({ payload = {}, data = {}, status }) {
  const source = payload && Object.keys(payload).length > 0 ? payload : data;
  const title = source.title || 'Backstage SetBoard';
  const setTitle = source.set_title || 'Untitled Set';
  const cards = Array.isArray(source.cards) ? source.cards : [];
  const direction = source.recommended_direction || 'Observational';
  const avgScore = Number.isFinite(source.average_score) ? source.average_score : 0;
  const topBits = Array.isArray(source.top_bits) ? source.top_bits : [];
  const riskNotes = Array.isArray(source.risk_notes) ? source.risk_notes : [];
  const brief = source.set_brief || source?.brief_packet?.canonical_description || '';
  const mode = source.presentation_mode || 'artifact';
  const inlineMode = mode === 'inline';
  const cardsToRender = inlineMode ? cards.slice(0, 2) : cards;
  const tone = laneTone(direction);

  return (
    <div style={{
      margin: inlineMode ? '10px auto' : '18px auto',
      maxWidth: inlineMode ? '740px' : '960px',
      borderRadius: '24px',
      border: `1px solid ${tone.glow}`,
      background: 'radial-gradient(circle at top left, rgba(255,255,255,0.08), transparent 32%), linear-gradient(160deg, rgba(18,18,28,0.98), rgba(12,10,18,0.98))',
      padding: inlineMode ? '16px' : '22px',
      color: '#f8fafc',
      boxShadow: `0 24px 56px ${tone.glow}`,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '14px', alignItems: 'flex-start', marginBottom: '16px' }}>
        <div>
          <div style={{
            display: 'inline-block',
            padding: '5px 10px',
            borderRadius: '999px',
            border: '1px solid rgba(148,163,184,0.2)',
            background: 'rgba(15,23,42,0.55)',
            color: '#cbd5e1',
            fontSize: '11px',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            marginBottom: '10px',
          }}>
            {inlineMode ? 'inline pulse' : 'artifact board'}
          </div>
          <h3 style={{ margin: 0, fontSize: inlineMode ? '22px' : '30px', lineHeight: 1.05 }}>{title}</h3>
          <div style={{ marginTop: '6px', fontSize: inlineMode ? '17px' : '21px', color: '#f9a8d4', fontWeight: 600 }}>
            {setTitle}
          </div>
          {brief ? (
            <div style={{ marginTop: '8px', maxWidth: '620px', color: '#cbd5e1', fontSize: '14px', lineHeight: 1.45 }}>
              {brief}
            </div>
          ) : null}
        </div>
        <div style={{ textAlign: 'right', minWidth: inlineMode ? '160px' : '200px' }}>
          <div style={{
            display: 'inline-block',
            padding: '5px 12px',
            borderRadius: '999px',
            border: `1px solid ${tone.accent}`,
            color: tone.accent,
            fontSize: '12px',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            marginBottom: '10px',
          }}>
            {direction}
          </div>
          <div style={{ fontSize: inlineMode ? '14px' : '16px', color: '#cbd5e1' }}>Room score</div>
          <div style={{ fontSize: inlineMode ? '28px' : '38px', fontWeight: 700 }}>{avgScore}<span style={{ fontSize: '14px', color: '#94a3b8' }}>/100</span></div>
        </div>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: inlineMode ? '1fr' : 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: '12px',
      }}>
        {cardsToRender.map((card, index) => {
          const cardTone = laneTone(card.lane);
          return (
            <div key={card.task_key || index} style={{
              border: '1px solid rgba(148,163,184,0.16)',
              borderRadius: '18px',
              padding: '14px',
              background: 'linear-gradient(180deg, rgba(15,23,42,0.72), rgba(15,23,42,0.42))',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', marginBottom: '8px' }}>
                <strong style={{ fontSize: '15px' }}>{card.lane || 'Lane'}</strong>
                <span style={{ fontSize: '12px', color: cardTone.accent }}>{card.score}/100</span>
              </div>
              <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {card.headline || 'headline pending'}
              </div>
              <div style={{ fontSize: '14px', lineHeight: 1.45, marginBottom: '8px' }}>
                {card.bit || 'No bit generated.'}
              </div>
              {card.tag ? (
                <div style={{ fontSize: '12px', color: '#86efac', marginBottom: '5px' }}>Tag: {card.tag}</div>
              ) : null}
              {card.crowd_hook ? (
                <div style={{ fontSize: '12px', color: '#7dd3fc', marginBottom: '5px' }}>Crowd hook: {card.crowd_hook}</div>
              ) : null}
              {card.closer_idea ? (
                <div style={{ fontSize: '12px', color: '#f9a8d4', marginBottom: '5px' }}>Closer: {card.closer_idea}</div>
              ) : null}
              {card.risk_note ? (
                <div style={{ fontSize: '12px', color: '#fda4af' }}>Risk: {card.risk_note}</div>
              ) : null}
            </div>
          );
        })}
      </div>

      {topBits.length > 0 ? (
        <div style={{ marginTop: '14px' }}>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Strongest bits
          </div>
          <ul style={{ margin: 0, paddingLeft: '18px', color: '#f1f5f9' }}>
            {topBits.map((item, idx) => <li key={idx}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {riskNotes.length > 0 ? (
        <div style={{ marginTop: '14px' }}>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Top risk notes
          </div>
          <ul style={{ margin: 0, paddingLeft: '18px', color: '#fda4af' }}>
            {riskNotes.map((item, idx) => <li key={idx}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {inlineMode ? (
        <div style={{ marginTop: '12px', fontSize: '12px', color: '#93c5fd' }}>
          Full set board is opening in the artifact panel.
        </div>
      ) : null}

      {status === 'running' ? (
        <div style={{ marginTop: '12px', fontSize: '12px', color: '#67e8f9' }}>Refreshing writers room...</div>
      ) : null}
    </div>
  );
}
