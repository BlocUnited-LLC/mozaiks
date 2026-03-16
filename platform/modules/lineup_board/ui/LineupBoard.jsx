import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI, useTheme, Header, Footer } from '@mozaiks/chat-ui';
import { executeModule } from '@mozaiks/chat-ui/coreBridge';

const FALLBACK_LINEUP = [
  {
    slot: '09:00 PM',
    performer: 'Startup Guy at Brunch',
    status: 'ready',
    direction: 'RoastLaneAgent',
    headline: 'Networking at brunch is just hostage negotiation with oat milk.',
  },
  {
    slot: '09:12 PM',
    performer: 'My Friend the Wellness Cultist',
    status: 'warming_up',
    direction: 'ObservationalLaneAgent',
    headline: 'Wellness people always look two sips away from selling you pond water.',
  },
  {
    slot: '09:24 PM',
    performer: 'Dating App Ghost Story',
    status: 'queued',
    direction: 'AbsurdistLaneAgent',
    headline: 'My dating profile needs a priest, not a photographer.',
  },
];

const statusTone = {
  ready: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/30',
  warming_up: 'bg-amber-500/20 text-amber-300 border-amber-400/30',
  queued: 'bg-sky-500/20 text-sky-300 border-sky-400/30',
};

export default function LineupBoard() {
  const navigate = useNavigate();
  const { user, loading, logout } = useChatUI();
  const { theme: chatTheme, loading: themeLoading } = useTheme();
  const [lineup, setLineup] = useState(FALLBACK_LINEUP);
  const [source, setSource] = useState('mock');

  const handleHeaderAction = (actionId, action = null) => {
    if (actionId === 'discover') {
      navigate('/discover');
      return;
    }
    if (actionId === 'signout' || action?.action === 'signout') {
      logout();
      return;
    }
    const target = action?.path || action?.href;
    if (!target) return;
    if (/^https?:\/\//i.test(target)) {
      window.location.href = target;
      return;
    }
    navigate(target);
  };

  useEffect(() => {
    let cancelled = false;
    const loadLineup = async () => {
      try {
        const response = await executeModule('lineup_board', { action: 'list_lineup' });
        if (cancelled) return;
        const rows = Array.isArray(response?.lineup) ? response.lineup : [];
        setLineup(rows.length > 0 ? rows : FALLBACK_LINEUP);
        setSource(response?.source === 'runtime' ? 'runtime' : 'mock');
      } catch (_err) {
        if (!cancelled) {
          setLineup(FALLBACK_LINEUP);
          setSource('mock');
        }
      }
    };
    loadLineup();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="min-h-screen bg-neutral-950 flex items-center justify-center"><div className="animate-spin rounded-full h-12 w-12 border-b-2 border-cyan-500"></div></div>;
  }

  return (
    <div className="min-h-screen flex flex-col bg-[radial-gradient(circle_at_top_left,_rgba(56,189,248,0.18),_transparent_28%),linear-gradient(180deg,_#09111f,_#100d1a)]">
      <Header user={user} chatTheme={chatTheme} themeLoading={themeLoading} onAction={handleHeaderAction} />
      <main className="flex-1 pt-20 pb-12 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="mb-8">
            <h1 className="text-3xl font-bold text-white">Lineup Board</h1>
            <p className="text-slate-400 mt-2">
              Live room order for Backstage sets. Source: {source === 'runtime' ? ' live MainStage results' : ' mock showcase data'}.
            </p>
          </div>

          <div className="space-y-4">
            {lineup.map((entry, idx) => (
              <div key={`${entry.slot}-${entry.performer}-${idx}`} className="rounded-3xl border border-white/10 bg-white/5 backdrop-blur-sm px-5 py-5 shadow-[0_24px_60px_rgba(0,0,0,0.24)]">
                <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
                  <div className="flex items-start gap-5">
                    <div className="w-20 shrink-0">
                      <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Slot</div>
                      <div className="text-xl font-semibold text-cyan-300 mt-1">{entry.slot}</div>
                    </div>
                    <div>
                      <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Performer</div>
                      <h2 className="text-2xl font-semibold text-white mt-1">{entry.performer}</h2>
                      <div className="text-sm text-pink-300 mt-2">{entry.direction}</div>
                      <p className="text-slate-200 mt-3 leading-6 max-w-3xl">{entry.headline}</p>
                    </div>
                  </div>
                  <div className={`self-start rounded-full border px-4 py-2 text-xs uppercase tracking-[0.22em] ${statusTone[entry.status] || statusTone.queued}`}>
                    {String(entry.status).replace('_', ' ')}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </main>
      <Footer chatTheme={chatTheme} />
    </div>
  );
}
