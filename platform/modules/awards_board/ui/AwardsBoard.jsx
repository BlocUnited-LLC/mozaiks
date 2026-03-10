/**
 * AwardsBoard — playful leaderboard for roast winners.
 *
 * Platform module UI for awards_board.
 * Rendered at /awards — registered via @modules auto-discovery.
 *
 * @module platform/modules/awards_board/ui/AwardsBoard
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
  useChatUI,
  Header,
  Footer,
  Card,
  Stat,
} from '@mozaiks/chat-ui';
import { executeModule } from '@mozaiks/chat-ui/coreBridge';

const FALLBACK_WINNERS = [
  {
    rank: 1,
    name: "Nia 'Risk Engine' Carter",
    award_title: 'Most Brutally Honest Pivot',
    score: 96,
    episode: 'Roast Night #12',
    trait_focus: 'Obsessive iteration speed',
  },
  {
    rank: 2,
    name: 'Marco Velasquez',
    award_title: 'Moonshot With Receipts',
    score: 92,
    episode: 'Roast Night #11',
    trait_focus: 'Big vision plus testable milestones',
  },
  {
    rank: 3,
    name: 'Priya Kline',
    award_title: 'Customer Pain Whisperer',
    score: 89,
    episode: 'Roast Night #10',
    trait_focus: 'High empathy and sharp objection handling',
  },
  {
    rank: 4,
    name: 'Darnell Brooks',
    award_title: 'Comeback Architect',
    score: 87,
    episode: 'Roast Night #09',
    trait_focus: 'Turned judge objections into roadmap wins',
  },
  {
    rank: 5,
    name: 'Elena Park',
    award_title: 'Most Chaotic, Still Works',
    score: 84,
    episode: 'Roast Night #08',
    trait_focus: 'Wild concept with surprisingly clean execution',
  },
];

const rankColor = (rank) => {
  if (rank === 1) return 'text-amber-300';
  if (rank === 2) return 'text-slate-200';
  if (rank === 3) return 'text-orange-300';
  return 'text-cyan-300';
};

const AwardsBoard = () => {
  const { user, loading } = useChatUI();
  const [winners, setWinners] = useState(FALLBACK_WINNERS);
  const [source, setSource] = useState('mock');
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const loadWinners = async () => {
      try {
        const response = await executeModule('awards_board', { action: 'list_winners' });
        if (cancelled) return;

        const rows = Array.isArray(response?.winners) ? response.winners : [];
        const resolvedSource = response?.source === 'runtime' ? 'runtime' : 'mock';
        if (rows.length > 0) {
          setWinners(rows);
          setSource(resolvedSource);
        } else {
          setWinners(FALLBACK_WINNERS);
          setSource('mock');
        }
      } catch (_err) {
        if (!cancelled) {
          setWinners(FALLBACK_WINNERS);
          setSource('mock');
        }
      } finally {
        if (!cancelled) setFetching(false);
      }
    };

    loadWinners();
    return () => { cancelled = true; };
  }, []);

  const summary = useMemo(() => {
    const total = winners.length;
    const avg = total > 0
      ? Math.round(winners.reduce((acc, row) => acc + Number(row.score || 0), 0) / total)
      : 0;
    const latestEpisode = winners[0]?.episode || 'n/a';
    const champion = winners.find((row) => Number(row.rank) === 1) || winners[0] || null;
    return { total, avg, latestEpisode, champion };
  }, [winners]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-cyan-500"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      <Header user={user} />

      <main className="pt-20 pb-12 px-4">
        <div className="max-w-5xl mx-auto space-y-8">
          <div>
            <h1 className="text-2xl font-bold text-white">Awards Board</h1>
            <p className="text-slate-400 mt-1">
              Hall of fame for standout RoastChat winners. Source: {source === 'runtime' ? 'live RoastChat runtime results' : 'mock data'}.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <Card title="Total Winners">
              <Stat value={summary.total} label="All-time entries" color="cyan" />
            </Card>
            <Card title="Average Score">
              <Stat value={`${summary.avg}/100`} label="Across listed winners" color="green" />
            </Card>
            <Card title="Latest Episode">
              <Stat value={summary.latestEpisode} label="Most recent board update" color="purple" />
            </Card>
          </div>

          {summary.champion && (
            <Card title="Current Champion" subtitle={summary.champion.award_title}>
              <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                <div>
                  <p className="text-xl font-semibold text-amber-300">{summary.champion.name}</p>
                  <p className="text-slate-300 mt-1">{summary.champion.trait_focus}</p>
                  <p className="text-slate-500 text-sm mt-2">{summary.champion.episode}</p>
                </div>
                <div className="text-right">
                  <p className="text-4xl font-bold text-amber-300">{summary.champion.score}</p>
                  <p className="text-slate-400 text-sm">jury score</p>
                </div>
              </div>
            </Card>
          )}

          <Card
            title="Leaderboard"
            subtitle={
              fetching
                ? 'Loading winners...'
                : (source === 'runtime'
                    ? 'Derived from completed RoastChat runs'
                    : 'Mock showcase data')
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px]">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-400 border-b border-slate-700/60">
                    <th className="py-3 pr-4">Rank</th>
                    <th className="py-3 pr-4">Winner</th>
                    <th className="py-3 pr-4">Award</th>
                    <th className="py-3 pr-4">Trait Focus</th>
                    <th className="py-3 pr-4">Episode</th>
                    <th className="py-3 text-right">Score</th>
                  </tr>
                </thead>
                <tbody>
                  {winners.map((row) => (
                    <tr
                      key={`${row.rank}-${row.name}`}
                      className="border-b border-slate-800/70 hover:bg-slate-800/35 transition-colors"
                    >
                      <td className={`py-3 pr-4 font-semibold ${rankColor(Number(row.rank))}`}>#{row.rank}</td>
                      <td className="py-3 pr-4 text-white font-medium">{row.name}</td>
                      <td className="py-3 pr-4 text-slate-200">{row.award_title}</td>
                      <td className="py-3 pr-4 text-slate-300">{row.trait_focus}</td>
                      <td className="py-3 pr-4 text-slate-400">{row.episode}</td>
                      <td className="py-3 text-right text-cyan-300 font-semibold">{row.score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </main>

      <Footer />
    </div>
  );
};

export default AwardsBoard;
