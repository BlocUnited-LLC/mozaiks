import { useEffect, useMemo, useRef, useState } from 'react';
import { StatusPill, SurfaceCard } from '../../ui/primitives/index.js';

function looksLikeMermaid(diagramText, diagramType) {
  const normalizedType = String(diagramType || '').trim().toLowerCase();
  if (normalizedType === 'mermaid' || normalizedType === 'sequence') {
    return true;
  }
  const normalizedText = String(diagramText || '').trim().toLowerCase();
  return (
    normalizedText.startsWith('sequencediagram')
    || normalizedText.startsWith('flowchart')
    || normalizedText.startsWith('graph ')
    || normalizedText.startsWith('statediagram')
    || normalizedText.startsWith('classdiagram')
  );
}

async function ensureMermaidLoaded() {
  if (typeof window === 'undefined') {
    return null;
  }
  if (window.mermaid) {
    return window.mermaid;
  }
  await new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js';
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
  return window.mermaid || null;
}

export default function DiagramViewer({ payload = {} }) {
  const checkpoints = Array.isArray(payload.checkpoints) ? payload.checkpoints : [];
  const legend = Array.isArray(payload.legend) ? payload.legend : [];
  const notes = payload.notes ? String(payload.notes) : '';
  const diagramRef = useRef(null);
  const [renderError, setRenderError] = useState('');
  const diagramText = useMemo(
    () => String(payload.diagram || payload.mermaid_diagram || payload.content || '').trim(),
    [payload.content, payload.diagram, payload.mermaid_diagram],
  );
  const useMermaidRenderer = useMemo(
    () => looksLikeMermaid(diagramText, payload.diagram_type),
    [diagramText, payload.diagram_type],
  );

  useEffect(() => {
    let cancelled = false;

    async function renderMermaidDiagram() {
      if (!useMermaidRenderer || !diagramRef.current) {
        return;
      }
      if (!diagramText) {
        diagramRef.current.innerHTML = '';
        return;
      }

      try {
        const mermaid = await ensureMermaidLoaded();
        if (!mermaid || cancelled || !diagramRef.current) {
          return;
        }
        const elementId = `diagram-viewer-${Math.random().toString(36).slice(2)}`;
        mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          securityLevel: 'loose',
          themeVariables: {
            primaryColor: '#1e40af',
            primaryTextColor: '#f8fafc',
            lineColor: '#94a3b8',
            noteBkgColor: '#0f172a',
            noteTextColor: '#e2e8f0',
            noteBorderColor: '#334155',
          },
        });
        const { svg } = await mermaid.render(elementId, diagramText);
        if (cancelled || !diagramRef.current) {
          return;
        }
        diagramRef.current.innerHTML = svg;
        const svgElement = diagramRef.current.querySelector('svg');
        if (svgElement) {
          svgElement.style.maxWidth = '100%';
          svgElement.style.width = '100%';
          svgElement.style.height = 'auto';
        }
        setRenderError('');
      } catch (error) {
        if (!cancelled) {
          setRenderError(error instanceof Error ? error.message : String(error));
        }
      }
    }

    renderMermaidDiagram();

    return () => {
      cancelled = true;
    };
  }, [diagramText, useMermaidRenderer]);

  return (
    <SurfaceCard
      title={payload.title || 'Diagram viewer'}
      subtitle={payload.summary || payload.notes || 'Read-only diagram artifact.'}
      headerAction={<StatusPill label={payload.diagram_type || (useMermaidRenderer ? 'mermaid' : 'artifact')} tone="default" />}
    >
      <div className="space-y-4">
        {useMermaidRenderer ? (
          <div className="rounded-md border border-border/60 bg-muted/40 p-4">
            <div ref={diagramRef} className="overflow-x-auto" />
            {renderError ? (
              <pre className="mt-3 overflow-x-auto rounded-md border border-destructive/40 bg-background p-3 text-xs leading-6 text-foreground">
                {diagramText}
              </pre>
            ) : null}
          </div>
        ) : (
          <pre className="overflow-x-auto rounded-md border border-border/60 bg-muted/40 p-4 text-xs leading-6 text-foreground">
            {diagramText}
          </pre>
        )}

        {renderError ? (
          <div className="rounded-md border border-destructive/40 bg-background px-3 py-3 text-sm text-foreground">
            Mermaid render failed: {renderError}
          </div>
        ) : null}

        {legend.length > 0 ? (
          <div className="rounded-md border border-border/60 bg-background px-3 py-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Legend</p>
            <ul className="mt-2 space-y-1 text-sm text-foreground">
              {legend.map((entry, index) => <li key={`${entry}-${index}`}>{String(entry)}</li>)}
            </ul>
          </div>
        ) : null}

        {checkpoints.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-3">
            {checkpoints.map((checkpoint, index) => (
              <div key={`${checkpoint?.label || index}`} className="rounded-md border border-border/60 bg-background px-3 py-3">
                <p className="text-sm font-medium text-foreground">{checkpoint?.label || `Checkpoint ${index + 1}`}</p>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">{checkpoint?.status || 'noted'}</p>
              </div>
            ))}
          </div>
        ) : null}

        {notes ? (
          <div className="rounded-md border border-border/60 bg-background px-3 py-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Notes</p>
            <p className="mt-2 text-sm text-foreground">{notes}</p>
          </div>
        ) : null}
      </div>
    </SurfaceCard>
  );
}
