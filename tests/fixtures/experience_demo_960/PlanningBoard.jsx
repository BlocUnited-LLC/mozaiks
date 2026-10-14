import React, { useCallback, useEffect, useState } from 'react';
import { moduleAction } from '../../lib/moduleApi.js';

const STATES = [
  { id: 'todo', label: 'Ready', tone: 'border-sky-300/30' },
  { id: 'doing', label: 'In motion', tone: 'border-amber-300/30' },
  { id: 'done', label: 'Done', tone: 'border-emerald-300/30' },
];

async function taskAction(action, values = {}) {
  return moduleAction('tasks', action, values);
}

export default function PlanningBoard() {
  const [tasks, setTasks] = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const result = await taskAction('list_tasks');
      setTasks(result.items || []);
      setError('');
    } catch (cause) { setError(cause.message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  async function move(task, status) {
    if (busy || task.status === status) return;
    setBusy(true);
    try {
      await taskAction('update_task', { task_id: task.task_id, status });
      await refresh();
      setSelected((current) => current?.task_id === task.task_id ? { ...current, status } : current);
    } catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }

  const active = tasks.find((task) => task.task_id === selected?.task_id) || selected;
  return (
    <main className="min-h-screen bg-[#f7f6f2] px-4 py-8 text-[#1b2831] sm:px-7" data-testid="planning-board">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#226276]">Projects / Planning</p>
            <h1 className="mt-2 text-4xl font-semibold tracking-tight">The work, in motion.</h1>
            <p className="mt-2 text-[#53646e]">Move a card, or open it to see the context. Changes are saved to your project.</p>
          </div>
          <a href="/projects" className="rounded-xl border border-[#9aadb0] px-4 py-2 font-semibold hover:bg-white">Project details</a>
        </header>
        {error && <div role="alert" className="mb-5 rounded-xl border border-red-300 bg-red-50 p-4 text-red-900">{error} <button type="button" onClick={refresh} className="ml-2 underline">Retry</button></div>}
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
          <section className="grid gap-4 md:grid-cols-3" aria-label="Planning board">
            {STATES.map((state) => <div key={state.id} className={`min-h-72 rounded-2xl border ${state.tone} bg-white/80 p-4 shadow-sm`}>
              <h2 className="mb-4 flex items-center justify-between font-semibold"><span>{state.label}</span><span className="rounded-full bg-[#e9eef0] px-2.5 py-1 text-xs">{tasks.filter((task) => task.status === state.id).length}</span></h2>
              <div className="space-y-3">
                {tasks.filter((task) => task.status === state.id).map((task) => <article key={task.task_id} className="rounded-xl border border-[#dce5e5] bg-white p-4 shadow-sm" draggable onDragStart={(event) => event.dataTransfer.setData('text/plain', task.task_id)}>
                  <button type="button" onClick={() => setSelected(task)} className="w-full text-left font-semibold hover:underline focus:outline-2 focus:outline-[#226276]">{task.title}</button>
                  <p className="mt-2 line-clamp-2 text-sm text-[#53646e]">{task.description || 'No description yet'}</p>
                  <label className="mt-3 block text-xs font-medium text-[#53646e]">Status
                    <select aria-label={`Status for ${task.title}`} value={task.status} disabled={busy} onChange={(event) => move(task, event.target.value)} className="mt-1 w-full rounded-lg border border-[#afc0c3] bg-white p-2 text-sm text-[#1b2831]">
                      {STATES.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                    </select>
                  </label>
                </article>)}
                <div onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const task = tasks.find((item) => item.task_id === event.dataTransfer.getData('text/plain')); if (task) move(task, state.id); }} className="rounded-xl border border-dashed border-[#a9b9bb] p-3 text-center text-xs text-[#647782]">Drop a card here</div>
              </div>
            </div>)}
            {loading && <p role="status">Loading the planning board…</p>}
          </section>
          <aside className="self-start rounded-2xl border border-[#dce5e5] bg-white p-5 shadow-sm" aria-label="Task inspector">
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-[#226276]">Task inspector</p>
            {active ? <div className="mt-4"><h2 className="text-2xl font-semibold">{active.title}</h2><p className="mt-3 whitespace-pre-wrap text-[#53646e]">{active.description || 'No description yet.'}</p><dl className="mt-5 border-t border-[#e2e9e9] pt-4 text-sm"><dt className="text-[#647782]">Status</dt><dd className="mt-1 font-semibold capitalize">{active.status}</dd><dt className="mt-4 text-[#647782]">Project</dt><dd className="mt-1 font-semibold">{active.project_id}</dd></dl></div> : <p className="mt-4 text-sm text-[#647782]">Select a card to inspect its details.</p>}
          </aside>
        </div>
      </div>
    </main>
  );
}
