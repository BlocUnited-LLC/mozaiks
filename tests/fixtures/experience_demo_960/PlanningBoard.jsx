import React, { useCallback, useEffect, useState } from 'react';
import { Button, CollectionToolbar } from '@mozaiks/chat-ui/ui';
import { moduleAction } from '../../lib/moduleApi.js';

const PAGE_SIZE = 8;
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
  const [query, setQuery] = useState({ page: 1, search: '' });
  const [total, setTotal] = useState(null);
  const [revision, setRevision] = useState(0);
  const requestSequence = React.useRef(0);

  const refresh = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setTasks([]);
    setTotal(null);
    setError('');
    try {
      const result = await taskAction('list_tasks', { ...query, page_size: PAGE_SIZE });
      if (sequence !== requestSequence.current) return;
      if (!Number.isSafeInteger(result.total) || result.total < 0 || !Array.isArray(result.items)) {
        throw new Error('Invalid task page response.');
      }
      const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
      if (query.page > lastPage) {
        setSelected(null);
        setQuery((current) => ({ ...current, page: lastPage }));
        return;
      }
      const expected = Math.min(PAGE_SIZE, Math.max(0, result.total - (query.page - 1) * PAGE_SIZE));
      if (result.items.length !== expected || result.items.some((task) => !task || typeof task.task_id !== 'string')) {
        throw new Error('Task rows do not match the requested page.');
      }
      setTasks(result.items);
      setTotal(result.total);
    } catch (cause) { if (sequence === requestSequence.current) setError(cause.message); }
    finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [query]);
  useEffect(() => {
    refresh();
    return () => { requestSequence.current += 1; };
  }, [refresh, revision]);

  function changeQuery(update) {
    requestSequence.current += 1;
    setSelected(null);
    setTasks([]);
    setTotal(null);
    setLoading(true);
    setQuery((current) => ({ ...current, ...update, ...(update.search !== undefined ? { page: 1 } : {}) }));
  }

  async function move(task, status) {
    if (busy || task.status === status) return;
    setBusy(true);
    try {
      await taskAction('update_task', { task_id: task.task_id, status });
      setRevision((current) => current + 1);
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
        <CollectionToolbar searchValue={query.search} onSearchChange={(search) => changeQuery({ search })} searchPlaceholder="Search tasks..." className="mb-5" />
        {total !== null && <div className="mb-5 flex flex-wrap items-center justify-between gap-3 text-sm">
          <p><span>{total} results</span> <span className="text-muted-foreground">— columns show this page only.</span></p>
          <nav aria-label="Task pages" className="flex items-center gap-3">
            <Button label="Previous page" variant="secondary" disabled={loading || query.page <= 1} onClick={() => changeQuery({ page: query.page - 1 })} />
            <span aria-live="polite">Page {query.page} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
            <Button label="Next page" variant="secondary" disabled={loading || query.page * PAGE_SIZE >= total} onClick={() => changeQuery({ page: query.page + 1 })} />
          </nav>
        </div>}
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
            {!loading && !error && total === 0 && <p role="status">No matching tasks.</p>}
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
