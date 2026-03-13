import React from 'react';

export default function FinalSetCard({ payload = {}, data = {}, status }) {
  const source = payload && Object.keys(payload).length > 0 ? payload : data;
  const title = source.title || 'Main Stage Final Set';
  const setTitle = source.set_title || 'Untitled Set';
  const direction = source.final_direction || 'Observational';
  const opening = source.opening_line || 'Opening line not set.';
  const middleBits = Array.isArray(source.middle_bits) ? source.middle_bits : [];
  const closer = source.closer || 'Closer not set.';
  const cleanAlt = source.clean_alt || '';
  const riskNotes = Array.isArray(source.risk_notes) ? source.risk_notes : [];
  const mode = source.presentation_mode || 'artifact';
  const inlineMode = mode === 'inline';

  return (
    <div style={{
      margin: inlineMode ? '10px auto' : '18px auto',
      maxWidth: inlineMode ? '760px' : '980px',
      borderRadius: '26px',
      border: '1px solid rgba(244,114,182,0.24)',
      background: 'radial-gradient(circle at top right, rgba(244,114,182,0.16), transparent 28%), linear-gradient(155deg, rgba(16,24,40,0.98), rgba(22,12,24,0.98))',
      padding: inlineMode ? '18px' : '24px',
      color: '#f8fafc',
      boxShadow: '0 26px 60px rgba(244,114,182,0.16)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', alignItems: 'flex-start', marginBottom: '16px' }}>
        <div>
          <div style={{
            display: 'inline-block',
            padding: '5px 10px',
            borderRadius: '999px',
            border: '1px solid rgba(244,114,182,0.24)',
            color: '#f9a8d4',
            fontSize: '11px',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            marginBottom: '10px',
          }}>
            {inlineMode ? 'inline preview' : 'artifact card'}
          </div>
          <h3 style={{ margin: 0, fontSize: inlineMode ? '22px' : '30px', lineHeight: 1.05 }}>{title}</h3>
          <div style={{ marginTop: '6px', fontSize: inlineMode ? '17px' : '22px', color: '#fde68a', fontWeight: 700 }}>
            {setTitle}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: '12px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Direction</div>
          <div style={{ fontSize: inlineMode ? '18px' : '24px', fontWeight: 700, color: '#f9a8d4' }}>{direction}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: inlineMode ? '1fr' : '1.1fr 0.9fr', gap: '14px' }}>
        <div style={{ border: '1px solid rgba(148,163,184,0.16)', borderRadius: '18px', padding: '14px', background: 'rgba(15,23,42,0.45)' }}>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Opening</div>
          <div style={{ fontSize: '15px', lineHeight: 1.5 }}>{opening}</div>

          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '16px', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Middle beats</div>
          <ol style={{ margin: 0, paddingLeft: '18px' }}>
            {middleBits.map((item, idx) => <li key={idx} style={{ marginBottom: '6px' }}>{item}</li>)}
          </ol>

          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '16px', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Closer</div>
          <div style={{ fontSize: '15px', lineHeight: 1.5 }}>{closer}</div>
        </div>

        <div style={{ border: '1px solid rgba(148,163,184,0.16)', borderRadius: '18px', padding: '14px', background: 'rgba(15,23,42,0.45)' }}>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Clean alternate</div>
          <div style={{ fontSize: '14px', lineHeight: 1.5 }}>{cleanAlt || 'No alternate generated.'}</div>

          {riskNotes.length > 0 ? (
            <>
              <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '16px', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Watchouts</div>
              <ul style={{ margin: 0, paddingLeft: '18px', color: '#fda4af' }}>
                {riskNotes.map((item, idx) => <li key={idx}>{item}</li>)}
              </ul>
            </>
          ) : null}
        </div>
      </div>

      {inlineMode ? (
        <div style={{ marginTop: '12px', fontSize: '12px', color: '#93c5fd' }}>
          The full final set card is opening in the artifact panel.
        </div>
      ) : null}

      {status === 'running' ? (
        <div style={{ marginTop: '12px', fontSize: '12px', color: '#67e8f9' }}>Refreshing main stage...</div>
      ) : null}
    </div>
  );
}
