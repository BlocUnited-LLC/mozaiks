import React, { useEffect, useMemo, useState } from 'react';
import { useChatUI, Header, Footer, Card, Stat } from '@mozaiks/chat-ui';
import { executeModule } from '@mozaiks/chat-ui/coreBridge';

const FALLBACK_SHOWS = [
  {
    rank: 1,
    set_title: 'Startup Guy at Brunch',
    direction: 'RoastLaneAgent',
    opening_line: 'You can always spot a startup founder because they network like the table is a hostage situation.',
    closer: 'Closer: circle back to the founder needing to pivot even his omelet.',
    episode: 'Backstage 2026-03-08 · A91C',
  },
  {
    rank: 2,
    set_title: 'My Friend the Wellness Cultist',
    direction: 'ObservationalLaneAgent',
    opening_line: 'Every wellness person says they want balance, then they hand you a powder that tastes like drywall and hope.',
    closer: 'Closer: admit the cult is just expensive soup with branding.',
    episode: 'Backstage 2026-03-06 · B203',
  },
];

export default function ShowArchive() {
  const { user, loading } = useChatUI();
  const [shows, setShows] = useState(FALLBACK_SHOWS);
  const [source, setSource] = useState('mock');

  useEffect(() => {
    let cancelled = false;
    const loadShows = async () => {
      try {
        const response = await executeModule('show_archive', { action: 'list_shows' });
        if (cancelled) return;
        const rows = Array.isArray(response?.shows) ? response.shows : [];
        setShows(rows.length > 0 ? rows : FALLBACK_SHOWS);
        setSource(response?.source === 'runtime' ? 'runtime' : 'mock');
      } catch (_err) {
        if (!cancelled) {
          setShows(FALLBACK_SHOWS);
          setSource('mock');
        }
      }
    };
    loadShows();
    return () => { cancelled = true; };
  }, []);

  const summary = useMemo(() => ({
    total: shows.length,
    headliner: shows[0] || null,
  }), [shows]);

  if (loading) {
    return <div className="min-h-screen bg-neutral-950 flex items-center justify-center"><div className="animate-spin rounded-full h-12 w-12 border-b-2 border-pink-500"></div></div>;
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(244,114,182,0.18),_transparent_28%),linear-gradient(180deg,_#0b0b14,_#161224)]">
      <Header user={user} />
      <main className="pt-20 pb-12 px-4">
        <div className="max-w-6xl mx-auto space-y-8">
          <div>
            <h1 className="text-3xl font-bold text-white">Show Archive</h1>
            <p className="text-slate-400 mt-2">
              Finished Backstage sets, saved closers, and stage-ready packets.
              Source: {source === 'runtime' ? ' live MainStage results' : ' mock showcase data'}.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card title="Archived Sets">
              <Stat value={summary.total} label="Saved stage packets" color="pink" />
            </Card>
            <Card title="Current Headliner">
              <Stat value={summary.headliner?.set_title || 'n/a'} label={summary.headliner?.direction || 'No archived sets yet'} color="orange" />
            </Card>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {shows.map((show) => (
              <div key={`${show.rank}-${show.set_title}`} className="rounded-3xl border border-white/10 bg-white/5 backdrop-blur-sm p-6 shadow-[0_24px_60px_rgba(0,0,0,0.28)]">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-xs uppercase tracking-[0.25em] text-pink-300">#{show.rank} archived set</div>
                    <h2 className="text-2xl font-semibold text-white mt-2">{show.set_title}</h2>
                    <p className="text-sm text-amber-300 mt-2">{show.direction}</p>
                  </div>
                  <div className="text-right text-xs text-slate-400">{show.episode}</div>
                </div>
                <div className="mt-5 space-y-4">
                  <div>
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-400 mb-1">Opening</div>
                    <p className="text-slate-100 leading-6">{show.opening_line}</p>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-400 mb-1">Closer</div>
                    <p className="text-slate-200 leading-6">{show.closer}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
