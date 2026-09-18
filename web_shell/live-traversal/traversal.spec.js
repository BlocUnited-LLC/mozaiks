/**
 * The traversal: idea -> generated bundle, through the real Studio, real LLM
 * calls, real Mongo. Nothing mocked.
 *
 * Every rule here exists because an earlier run got it wrong:
 *  - the front door needs a retry; the first /api call after a cold backend can
 *    exceed the vite proxy timeout
 *  - approval gates are sometimes a checkbox *then* a button
 *  - gate labels are sentences ("Confirm Subscription Plan Contract"), so an
 *    anchored /^confirm$/i matches nothing
 *  - artifact panels are not in body.innerText, so measuring it called a live
 *    screen a stall
 *  - gates now carry data-testid="approval-action-<id>"; ids are contract,
 *    labels are model-authored prose
 */
import { test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, 'artifacts');
fs.mkdirSync(OUT, { recursive: true });

// Generated code lands in ChatSessions.generated_files in Mongo, not on disk.
// A harness watching generated/apps reports a finished build as NO BUNDLE —
// it did exactly that while a 17-file app sat in the database.
const VENV_PY = 'c:/Repos/BlocUnitedRepo/mozaiks/.venv/Scripts/python.exe';
const COUNTER = path.join(__dirname, 'count_generated.py');

const bundleState = () => {
  try {
    const raw = execFileSync(VENV_PY, [COUNTER], { encoding: 'utf8', timeout: 20000 });
    return JSON.parse(raw);
  } catch (err) {
    // Never let a measurement failure masquerade as "no bundle".
    note('BUNDLE_PROBE_FAILED', String(err.message || err).slice(0, 120));
    return null;
  }
};
const bundleCount = () => {
  const state = bundleState();
  return state === null ? -1 : state.bundles;
};

// Chats this run drove. Another agent finishing a build while we are mid-run
// raises the global count, and a harness that watches the count calls their
// bundle ours. On 2026-09-17 that reported BUNDLE PRODUCED for a habit tracker
// while this run was still in ThemeCapture building a tool-lending library.
const myChats = new Set();
const myBundle = () => {
  const state = bundleState();
  if (state === null) return undefined;          // probe failed: not a negative
  return (state.detail || []).find(
    (b) => myChats.has(String(b.chat_id)) && (b.file_count || 0) > 0,
  ) || null;
};

const log = [];
function note(stage, detail) {
  const line = `[${new Date().toISOString()}] ${stage}: ${detail}`;
  console.log(`TV ${line}`);
  log.push(line);
  fs.writeFileSync(path.join(OUT, 'traversal.log'), log.join('\n'), 'utf8');
}

const IDEA =
  'A tool-lending library for a neighbourhood association. Members browse a '
  + 'catalogue of tools, request to borrow one for a date range, an admin '
  + 'approves or declines, and the app tracks due dates and flags overdue items.';

// Substantive and non-repeating: a vague human is a different test, and an
// interview that receives "looks good" five times cannot converge.
// Each answer must carry information. Content-free affirmations ("confirmed",
// "go ahead") are what let an interview drift: the agent proposes, the harness
// agrees, and the concept walks away from the stated idea. A short decisive set
// converges; a long agreeable one trips the convergence guard.
const ANSWERS = [
  'Members are neighbours who sign in with email. Admins are two volunteer coordinators.',
  'Start with the catalogue and the borrow-request approval flow. Reminders can come second.',
  'Due-date tracking with an overdue list for admins is essential for version one.',
  'Free for members, no payments. No AI workflows — ordinary modules and pages cover it.',
  'Light mode, soft teal #2F9C95 primary with a warm sand accent, clean humanist sans, rounded corners.',
  'That matches the tool-lending library I described. Please proceed.',
  // Confirmations must re-anchor, not just agree. Bare assent lets the agent
  // build on its own speculation; restating the scope keeps the concept fixed
  // while still moving the interview forward.
  'Yes — catalogue, borrow requests with admin approval, due dates and an overdue list. Nothing beyond that for version one.',
  'Correct, that is the whole scope. Please produce the concept blueprint for it.',
  'Confirmed: a tool-lending library, no social features, no payments. Go ahead.',
  'Yes, that is the app. Proceed to the next stage.',
];

// Each stage of the build sequence runs its own interview, so a finite list
// starves partway through the journey. These cycle: because every line restates
// the scope rather than merely agreeing, repeating them cannot drift the concept
// the way bare assent did.
const ANCHORS = [
  'Yes — a tool-lending library: catalogue, borrow requests with admin approval, due dates, overdue list. Nothing more for version one.',
  'Correct as described. Light mode, soft teal #2F9C95 with a warm sand accent, clean humanist sans. Please continue.',
  'Confirmed: no payments, no social features, no AI workflows. Ordinary modules and pages. Proceed.',
  'That is the app. Go ahead with the next stage.',
];

// Forward-only. Prefix-matched, because real labels are sentences. Destructive
// and options that undo or cancel are never listed.
const GATES = [
  /^start build$/i,
  // Build mode. Unanchored on purpose: these are cards, and the accessible name
  // concatenates badge + title + description in DOM order, so the title is not
  // reliably first. Autonomous first — it satisfies fewer downstream gates.
  /build it for me/i, /build it with me/i,
  /^approve concept/i, /^approve plan/i,
  /^confirm subscription/i, /^confirm and continue/i,
  /^approve/i, /^confirm/i, /^accept/i,
  /^continue$/i, /^next$/i, /^proceed/i,
  /^start generation/i, /^generate/i, /^promote/i,
  /^skip for now$/i, /^use local mongodb$/i, /^autonomous$/i,
];
const REVERSING = new Set(['cancel', 'request_changes', 'reject', 'back']);

test('traversal: idea to generated bundle', async ({ page }) => {
  const start = bundleCount();
  if (start < 0) {
    note('RESULT', 'ABORTED - cannot read generated_files; a run measured by a broken probe proves nothing');
    return;
  }
  note('BASELINE', `${start} bundle(s) recorded`);

  page.on('console', (m) => {
    if (m.type() === 'error') note('BROWSER_ERROR', m.text().slice(0, 180));
  });
  // A run that cannot spend tokens is not a run. The balance gate rejects at
  // /api/transitions/resolve with a 400, the composer keeps accepting messages,
  // and the transcript looks normal — so a harness that only watches the screen
  // reports a stall and then NO BUNDLE. That is a false negative about the
  // product, dressed as evidence. Forty minutes of one run went that way.
  let blocked = null;
  page.on('response', async (r) => {
    if (r.status() >= 500) note('HTTP_5XX', `${r.status()} ${r.url().slice(0, 110)}`);
    if (blocked || r.status() !== 400 || !r.url().includes('/api/')) return;
    let detail = '';
    try { detail = (await r.text()).slice(0, 200); } catch { return; }
    if (/token balance|balance exhausted|upgrade your AI plan|insufficient tokens/i.test(detail)) {
      try { blocked = String(JSON.parse(detail).detail || detail); }
      catch { blocked = detail; }
      note('BLOCKED', blocked.slice(0, 120));
    }
  });

  await page.goto('/create');
  note('START', new URL(page.url()).pathname);

  const greenfield = page.getByRole('button', { name: /start build/i }).first();
  let ready = await greenfield.waitFor({ state: 'visible', timeout: 45000 })
    .then(() => true).catch(() => false);
  if (!ready) {
    note('RETRY', 'no Start Build yet; reloading');
    await page.reload();
    ready = await greenfield.waitFor({ state: 'visible', timeout: 45000 })
      .then(() => true).catch(() => false);
  }
  await page.screenshot({ path: path.join(OUT, '00-create.png'), fullPage: true }).catch(() => {});
  if (!ready) {
    note('RESULT', 'BLOCKED - no Start Build control');
    return;
  }
  await greenfield.click().catch(() => {});
  note('CHOSE', 'greenfield');
  await page.waitForTimeout(8000);

  const seen = new Set();
  let idx = 0, shots = 0, idle = 0, marker = 0, repliesHere = 0, failedIn = null;
  // A gate that changes nothing is not a gate. Clicking "Continue" ten times
  // because it stays on screen burned an entire run inside ThemeCapture while
  // the workflow was healthy and simply waiting for something else.
  const spent = new Map();
  const domNow = async () => ((await page.content().catch(() => '')) || '').length;
  const exhausted = (id) => (spent.get(id) || 0) >= 2;
  // Give every skipped gate another chance rather than idling forever: a gate
  // that looked inert may simply have been waiting on work that has now landed.
  const forgive = () => {
    if (!spent.size) return false;
    const retired = [...spent.entries()].filter(([, n]) => n >= 2).map(([id]) => id);
    if (!retired.length) return false;
    spent.clear();
    note('GATE_FORGIVEN', `nothing else to do; retrying ${retired.join(', ')}`);
    return true;
  };
  const record = async (id, before) => {
    const after = await domNow();
    if (Math.abs(after - before) < 200) {
      spent.set(id, (spent.get(id) || 0) + 1);
      if (exhausted(id)) note('GATE_INERT', `"${id}" changed nothing twice; ignoring it`);
    } else {
      spent.set(id, 0);
    }
  };
  const deadline = Date.now() + 40 * 60 * 1000;
  const wf = () => {
    try { return new URL(page.url()).searchParams.get('workflow') || '?'; } catch { return '?'; }
  };
  const trackChat = () => {
    try {
      const id = new URL(page.url()).searchParams.get('chat_id');
      if (id && !myChats.has(id)) { myChats.add(id); note('CHAT', id); }
    } catch { /* not on a chat route yet */ }
  };

  while (Date.now() < deadline) {
    if (blocked) break;
    const cur = wf();
    if (!seen.has(cur)) {
      seen.add(cur);
      repliesHere = 0;
      note('WORKFLOW', cur);
      await page.screenshot({ path: path.join(OUT, `10-${cur}.png`), fullPage: true }).catch(() => {});
    }
    trackChat();

    // The retry control only exists once the run has failed, so it is an
    // unambiguous signal -- unlike transcript text, which can say almost
    // anything. Never click it: the inputs that failed are still the inputs.
    const retry = page.getByRole('button', { name: /retry failed workflow/i }).first();
    if (await retry.count().catch(() => 0) && await retry.isVisible().catch(() => false)) {
      failedIn = cur;
      note('WORKFLOW_FAILED', `${cur} failed; not waiting out the stall timer`);
      await page.screenshot({ path: path.join(OUT, `85-failed-${cur}.png`), fullPage: true }).catch(() => {});
      break;
    }

    const mine = myBundle();
    if (mine) {
      note('BUNDLE', `${mine.file_count} files for OUR chat ${String(mine.chat_id).slice(0, 8)}…`);
      await page.screenshot({ path: path.join(OUT, '90-bundle.png'), fullPage: true }).catch(() => {});
      break;
    }

    // Tick any acknowledgement first; approvals are commonly disabled until then.
    for (const box of await page.getByRole('checkbox').all()) {
      if (await box.isVisible().catch(() => false)
          && await box.isEnabled().catch(() => false)
          && !(await box.isChecked().catch(() => true))) {
        await box.check({ force: true }).catch(() => {});
        note('ACK', `ticked a confirmation in ${cur}`);
        await page.waitForTimeout(1500);
      }
    }

    let acted = false;

    // Prefer the contract handle over the label.
    for (const el of await page.locator('[data-testid^="approval-action-"]').all()) {
      const id = ((await el.getAttribute('data-testid').catch(() => '')) || '')
        .replace('approval-action-', '');
      if (REVERSING.has(id)) continue;
      if (exhausted(`handle:${id}`)) continue;
      if (await el.isVisible().catch(() => false) && await el.isEnabled().catch(() => false)) {
        const before = await domNow();
        await el.scrollIntoViewIfNeeded().catch(() => {});
        await el.click().catch(() => {});
        note('GATE', `[${id}] by handle in ${cur}`);
        await page.waitForTimeout(9000);
        await record(`handle:${id}`, before);
        await page.screenshot({ path: path.join(OUT, `20-gate-${shots++}.png`), fullPage: true }).catch(() => {});
        acted = true;
        break;
      }
    }
    if (acted) { idle = 0; continue; }

    for (const rx of GATES) {
      const btn = page.getByRole('button', { name: rx }).first();
      if (await btn.count().catch(() => 0)
          && await btn.isVisible().catch(() => false)
          && await btn.isEnabled().catch(() => false)) {
        const label = (await btn.innerText().catch(() => '')).trim().slice(0, 40);
        if (exhausted(`label:${cur}:${label}`)) continue;
        const before = await domNow();
        await btn.scrollIntoViewIfNeeded().catch(() => {});
        await btn.click().catch(() => {});
        note('GATE', `"${label}" in ${cur}`);
        await page.waitForTimeout(9000);
        await record(`label:${cur}:${label}`, before);
        await page.screenshot({ path: path.join(OUT, `20-gate-${shots++}.png`), fullPage: true }).catch(() => {});
        acted = true;
        break;
      }
    }
    if (acted) { idle = 0; continue; }

    const composer = page.getByPlaceholder(/reply|message|ask|type/i).first();
    if (await composer.count().catch(() => 0)
        && await composer.isVisible().catch(() => false)
        && await composer.isEditable().catch(() => false)) {
      const text = idx === 0
        ? IDEA
        : (ANSWERS[idx - 1] ?? ANCHORS[(idx - 1 - ANSWERS.length) % ANCHORS.length]);
      await composer.click().catch(() => {});
      await composer.fill(text).catch(() => {});
      await page.keyboard.press('Enter').catch(() => {});
      note('REPLIED', `[${idx}] ${text.slice(0, 58)}`);
      idx += 1; idle = 0;
      repliesHere += 1;
      if (repliesHere % 6 === 0 && forgive()) { repliesHere += 0; }
      if (repliesHere === 12) {
        note('REPLY_LOOP', `12 replies inside ${cur} with no workflow change — likely an unrecognised gate`);
        for (const b of await page.getByRole('button').all()) {
          if (!(await b.isVisible().catch(() => false))) continue;
          const nm = ((await b.innerText().catch(() => '')) || '').trim().replace(/\s+/g, ' ');
          if (nm) note('UNMATCHED_BUTTON', `enabled=${await b.isEnabled().catch(() => false)} :: ${nm.slice(0, 90)}`);
        }
        await page.screenshot({ path: path.join(OUT, `80-reply-loop-${cur}.png`), fullPage: true }).catch(() => {});
      }
      await page.waitForTimeout(20000);
      continue;
    }

    await page.waitForTimeout(12000);
    // Serialized DOM, not innerText: artifact panels live outside body text.
    const dom = ((await page.content().catch(() => '')) || '').length;
    const buttons = await page.getByRole('button').count().catch(() => 0);
    const now = dom + buttons * 100000;
    if (now !== marker) {
      marker = now; idle = 0;
      note('TICK', `${cur} dom=${dom} buttons=${buttons}`);
    } else {
      idle += 1;
      if (idle % 10 === 0) note('IDLE', `${idle} rounds in ${cur}`);
    }
    if (idle >= 20 && forgive()) { idle = 0; continue; }
    if (idle >= 40) { note('STALLED', `${cur} unchanged ~8min`); break; }
  }

  await page.screenshot({ path: path.join(OUT, '99-final.png'), fullPage: true }).catch(() => {});
  const end = bundleCount();
  note('WORKFLOWS_SEEN', [...seen].join(' -> '));
  note('BUNDLES', `start=${start} end=${end}`);
  if (blocked) {
    note('RESULT', `BLOCKED - ${blocked.slice(0, 90)}`);
    note('NOTE', 'the product was never exercised; this says nothing about the build');
  } else if (end < 0) {
    note('RESULT', 'UNKNOWN - the bundle probe failed; not reporting a negative');
  } else if (failedIn) {
    const mine = myBundle();
    note('RESULT', `WORKFLOW FAILED in ${failedIn}`);
    note('NOTE', mine
      ? 'a bundle exists from an earlier stage; the run still did not complete'
      : 'check the backend log for the failure reason; the UI only says workflow_failed');
  } else {
    const mine = myBundle();
    if (mine === undefined) note('RESULT', 'UNKNOWN - the bundle probe failed; not reporting a negative');
    else if (mine) note('RESULT', `BUNDLE PRODUCED - ${mine.file_count} files, chat ${String(mine.chat_id).slice(0, 8)}`);
    else note('RESULT', `NO BUNDLE - none of our ${myChats.size} chat(s) produced files`);
  }
  note('FINAL_URL', page.url());
});
