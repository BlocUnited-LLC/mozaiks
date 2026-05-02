// =============================================================================
// FILE: ChatUI/src/workflows/AgentGenerator/components/MermaidSequenceDiagram.js
// PURPOSE: Artifact component that renders the Mermaid sequence diagram emitted
//          after the Action Plan has been approved.
// =============================================================================

import React, { useEffect, useRef, useState } from 'react';
import { GitBranch, ListChecks, StickyNote, CheckCircle2 } from 'lucide-react';
import { typography, components, colors } from '../../../../../chat-ui/src/platform/workflowSurfaceStyles.js';

const DEFAULT_PENDING_MESSAGE = 'Sequence diagram is on the way. Approve the action plan to trigger generation.';

const MermaidSequenceDiagram = ({
  payload = {},
  onResponse,
  toolName,
  toolCallId,
  workflowName,
  componentId = 'MermaidSequenceDiagram'
}) => {
  const mermaidRef = useRef(null);
  const [pending, setPending] = useState(false);

  const resolvedWorkflowName =
    (typeof payload?.workflow_name === 'string' && payload.workflow_name.trim()) ||
    (typeof workflowName === 'string' && workflowName.trim()) ||
    'Generated Workflow';

  const diagramText = typeof payload?.diagram === 'string' ? payload.diagram.trim() : '';
  const legendItems = Array.isArray(payload?.legend)
    ? payload.legend.filter(item => typeof item === 'string' && item.trim().length > 0)
    : [];
  const notes = typeof payload?.notes === 'string' && payload.notes.trim().length > 0 ? payload.notes.trim() : null;
  const agentMessageId = payload?.agent_message_id || payload?.agentMessageId || null;
  const displayMessage = typeof payload?.agent_message === 'string' && payload.agent_message.trim().length > 0
    ? payload.agent_message.trim()
    : 'Sequence diagram ready for review.';

  useEffect(() => {
    const render = async () => {
      if (!mermaidRef.current) return;

      if (!diagramText) {
        mermaidRef.current.innerHTML = `<div class="flex h-full items-center justify-center p-6 text-center text-sm text-slate-400">${DEFAULT_PENDING_MESSAGE}</div>`;
        return;
      }

      const normalized = diagramText.startsWith('sequenceDiagram') ? diagramText : `sequenceDiagram\n${diagramText}`;

      const safeRender = () => {
        try {
          const id = `seq-${Math.random().toString(36).slice(2)}`;
              const narrowMinHeight = (typeof window !== 'undefined' && window.innerWidth < 768) ? '260px' : '440px';
          const mermaidConfig = {
            startOnLoad: false,
            theme: 'dark',
            sequence: {
              diagramMarginX: 20,
              diagramMarginY: 40,
              actorMargin: 80,
              width: 200,
              height: 65,
              boxMargin: 10,
              boxTextMargin: 5,
              noteMargin: 15,
              messageMargin: 40,
              mirrorActors: false,
              useMaxWidth: true,
              wrap: true,
              wrapPadding: 10
            },
            themeVariables: {
              primaryColor: '#3b82f6',
              primaryBorderColor: '#60a5fa',
              primaryTextColor: '#ffffff',
              lineColor: '#94a3b8',
              noteBkgColor: '#1e293b',
              noteTextColor: '#e2e8f0',
              noteBorderColor: '#475569',
              activationBkgColor: '#2563eb',
              activationBorderColor: '#60a5fa',
              fontSize: '16px',
              fontFamily: 'ui-sans-serif, system-ui, sans-serif'
            }
          };

          window.mermaid.initialize(mermaidConfig);
          window.mermaid.render(id, normalized).then(({ svg }) => {
            if (mermaidRef.current) {
              mermaidRef.current.innerHTML = svg;
              const svgEl = mermaidRef.current.querySelector('svg');
              if (svgEl) {
                svgEl.style.maxWidth = '100%';
                    svgEl.style.width = '100%';
                    svgEl.style.height = 'auto';
                    svgEl.style.minHeight = narrowMinHeight;
                    svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
              }
            }
          }).catch((err) => {
            console.error('🎨 [MermaidSequenceDiagram] Render error:', err);
            if (mermaidRef.current) {
              mermaidRef.current.innerHTML = '<div class="flex h-full items-center justify-center text-sm text-[var(--color-error)]">Diagram render error</div>';
            }
          });
        } catch (err) {
          console.error('🎨 [MermaidSequenceDiagram] Render exception:', err);
          if (mermaidRef.current) {
            mermaidRef.current.innerHTML = '<div class="flex h-full items-center justify-center text-sm text-[var(--color-error)]">Diagram render error</div>';
          }
        }
      };

      if (!window || !window.document) return;
      if (!window.mermaid) {
        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js';
        script.onload = () => safeRender();
        script.onerror = () => {
          console.error('🎨 [MermaidSequenceDiagram] Failed to load Mermaid.js');
          if (mermaidRef.current) {
            mermaidRef.current.innerHTML = '<div class="flex h-full items-center justify-center text-sm text-[var(--color-error)]">Failed to load diagram library</div>';
          }
        };
        document.head.appendChild(script);
      } else {
        safeRender();
      }
    };

    render();
  }, [diagramText]);

  const emit = async (action) => {
    if (pending) return;
    setPending(true);
    try {
      const payloadData = {
        status: 'success',
        action,
        data: {
          action,
          workflow_name: resolvedWorkflowName,
          diagram_present: Boolean(diagramText),
          legend_count: legendItems.length,
          tool_name: toolName,
          tool_call_id: toolCallId,
          workflowName,
          agent_message_id: agentMessageId,
          component_id: componentId
        }
      };
      await onResponse?.(payloadData);
    } catch (err) {
      console.error('🎨 [MermaidSequenceDiagram] emit error:', err);
      onResponse?.({
        status: 'error',
        action,
        message: err?.message || 'Failed to acknowledge diagram',
        agent_message_id: agentMessageId
      });
    } finally {
      setPending(false);
    }
  };

  const acknowledge = () => emit('acknowledge_diagram');

  return (
    <div
      className={`space-y-6 rounded-2xl ${components.card.primary}`}
      data-agent-message-id={agentMessageId || undefined}
    >
      <header className="rounded-2xl border-3 border-[var(--color-secondary)] bg-gradient-to-r from-slate-900 via-slate-800 to-slate-900 p-6 shadow-2xl [box-shadow:0_0_0_rgba(var(--color-secondary-rgb),0.3)]">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-3">
            <div className="flex items-center gap-3 text-xs font-black uppercase tracking-[0.3em] text-[var(--color-secondary-light)]">
              <GitBranch className="h-4 w-4" />
              Sequence Diagram
            </div>
            <h1 className={`${typography.display.lg} ${colors.text.primary} break-words max-w-full leading-tight overflow-hidden`}>
              {resolvedWorkflowName}
            </h1>
            <p className="text-sm font-medium text-slate-300">{displayMessage}</p>
          </div>
          <button
            type="button"
            onClick={acknowledge}
            disabled={pending}
            className={`${components.button.primary} text-sm`}
          >
            <CheckCircle2 className="mr-2 h-4 w-4" />
            Continue
          </button>
        </div>
      </header>

      <div className="rounded-2xl border-2 border-slate-700 bg-slate-900/70">
        <div ref={mermaidRef} className="min-h-[320px] md:min-h-[420px] overflow-auto bg-slate-850 p-4 md:p-6 rounded-xl" />
      </div>

      {(legendItems.length > 0 || notes) && (
        <section className="grid gap-5 lg:grid-cols-2">
          {legendItems.length > 0 && (
            <div className="rounded-xl border-2 border-[rgba(var(--color-secondary-rgb),0.4)] bg-slate-800 p-6 shadow-lg">
              <div className="mb-3 flex items-center gap-2 text-sm font-black uppercase tracking-wider text-[var(--color-secondary-light)]">
                <ListChecks className="h-4 w-4" /> Legend
              </div>
              <ul className="space-y-2 text-sm text-slate-200">
                {legendItems.map((item, idx) => (
                  <li key={idx} className="flex items-start gap-2">
                    <span className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-secondary-light)]" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {notes && (
            <div className="rounded-xl border-2 border-[rgba(var(--color-primary-rgb),0.4)] bg-slate-800 p-6 shadow-lg">
              <div className="mb-3 flex items-center gap-2 text-sm font-black uppercase tracking-wider text-[var(--color-primary-light)]">
                <StickyNote className="h-4 w-4" /> Notes
              </div>
              <p className="text-sm leading-relaxed text-slate-200">{notes}</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
};

MermaidSequenceDiagram.displayName = 'MermaidSequenceDiagram';
export default MermaidSequenceDiagram;
