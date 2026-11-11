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
import { createNote, finishTraversal, requirePassedDraft, validatedDraftId, verifyTitleSearch } from './notes-journey.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, 'artifacts');
const notesMode = process.env.MOZAIKS_TRAVERSAL_NOTES === '1';
fs.mkdirSync(OUT, { recursive: true });

// Generated code lands in ChatSessions.generated_files in Mongo, not on disk.
// A harness watching generated/apps reports a finished build as NO BUNDLE —
// it did exactly that while a 17-file app sat in the database.
const PROBE = path.join(__dirname, 'run_evidence.py');
const REPO_ROOT = path.resolve(__dirname, '../..');

// An absolute interpreter path pinned this harness to one machine. Resolve it:
// explicit override, then a repo-local venv, then whatever is on PATH.
const pythonCandidates = () => {
  const explicit = process.env.MOZAIKS_TRAVERSAL_PYTHON;
  const venv = process.platform === 'win32'
    ? path.join(REPO_ROOT, '.venv/Scripts/python.exe')
    : path.join(REPO_ROOT, '.venv/bin/python');
  return [explicit, fs.existsSync(venv) ? venv : null, 'python3', 'python'].filter(Boolean);
};

let resolvedPython = null;
const runProbe = (args = []) => {
  const errors = [];
  for (const candidate of resolvedPython ? [resolvedPython] : pythonCandidates()) {
    try {
      const raw = execFileSync(candidate, [PROBE, ...args], { encoding: 'utf8', timeout: 30000 });
      resolvedPython = candidate;
      return JSON.parse(raw);
    } catch (err) {
      errors.push(`${candidate}: ${String(err.message || err).slice(0, 80)}`);
    }
  }
  // Never let a measurement failure masquerade as "no bundle".
  note('EVIDENCE_PROBE_FAILED', errors.join(' | ').slice(0, 300));
  return null;
};

const bundleState = () => runProbe();

// Chats this run drove. Another agent finishing a build while we are mid-run
// raises the global count, and a harness that watches the count calls their
// bundle ours. On 2026-09-17 that reported BUNDLE PRODUCED for a habit tracker
// while this run was still in ThemeCapture building a tool-lending library.
const myChats = new Set();
const myBundle = () => {
  const state = bundleState();
  if (state === null) return undefined;          // probe failed: not a negative
  return (state.bundles || []).find(
    (b) => myChats.has(String(b.chat_id)) && (b.file_count || 0) > 0,
  ) || null;
};

// ChatSessions.status (mozaiksai WorkflowStatus): 0 in progress, 1 completed, 2 failed.
const WORKFLOW_FAILED_STATUS = 2;

const myAcceptedAppBundle = () => {
  const state = bundleState();
  if (state === null) return undefined;
  return (state.artifacts || []).find(
    (a) => myChats.has(String(a.source_chat_id)) && a.build_family === 'app_bundle'
      && a.lifecycle_status === 'current' && a.app_validation_status === 'passed' && (a.file_count || 0) > 0,
  ) || null;
};

const makeBlockedEntryVerdict = (reason) => ({
  ok: false,
  outcome: 'BLOCKED',
  summary: `BLOCKED - ${String(reason).slice(0, 120)}`,
  notes: ['the build sequence did not start; no bundle claim is made'],
});

// What was running, so a result can be tied to the code that produced it. A
// whole session was once spent debugging fixes against a stack that did not
// contain them; the run looked identical either way.
const provenance = {
  started_at: new Date().toISOString(),
  package_path: null,
  package_version: null,
  oss_sha: null,
  oss_branch: null,
};

const recordProvenance = () => {
  try {
    provenance.oss_sha = execFileSync('git', ['rev-parse', 'HEAD'],
      { cwd: REPO_ROOT, encoding: 'utf8', timeout: 10000 }).trim();
    provenance.oss_branch = execFileSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'],
      { cwd: REPO_ROOT, encoding: 'utf8', timeout: 10000 }).trim();
  } catch (err) {
    note('PROVENANCE_PARTIAL', `git: ${String(err.message || err).slice(0, 80)}`);
  }
  // Ask the interpreter that will read the database, not this checkout: the
  // installed package is what the backend imports, and the two can differ.
  const script = 'import mozaiksai,json;'
    + 'from importlib.metadata import version;'
    + 'print(json.dumps({"path": mozaiksai.__file__, "version": version("mozaiks")}))';
  for (const candidate of resolvedPython ? [resolvedPython] : pythonCandidates()) {
    try {
      const raw = execFileSync(candidate, ['-c', script], { encoding: 'utf8', timeout: 20000 });
      const info = JSON.parse(raw);
      provenance.package_path = info.path;
      provenance.package_version = info.version;
      resolvedPython = candidate;
      break;
    } catch { /* try the next interpreter */ }
  }
  note('PROVENANCE', `sha=${provenance.oss_sha || '?'} branch=${provenance.oss_branch || '?'} `
    + `pkg=${provenance.package_version || '?'} at ${provenance.package_path || '?'}`);
};

// One place that decides pass or fail, so no outcome can be logged as a
// problem while the process exits 0.
const judge = ({ evidence, blocked, failedIn, chats }) => {
  const notes = [];
  if (blocked) {
    return { ok: false, outcome: 'BLOCKED', summary: `BLOCKED - ${String(blocked).slice(0, 120)}`,
      notes: ['the product was never exercised; this says nothing about the build'] };
  }
  if (evidence === null) {
    return { ok: false, outcome: 'UNKNOWN', summary: 'UNKNOWN - the evidence probe failed',
      notes: ['no claim either way; an unmeasured run is not a passing run'] };
  }

  const ours = [...chats];
  const bundles = (evidence.bundles || []).filter((b) => ours.includes(String(b.chat_id)) && (b.file_count || 0) > 0);
  const artifacts = (evidence.artifacts || []).filter((a) => ours.includes(String(a.source_chat_id)));
  // Only the app bundle is the build. Concept, theme, and design-docs versions
  // are accepted early and never carry an app validation, so counting them
  // reported "NOT VALIDATED" for a build that had not finished (2026-11-02).
  const accepted = artifacts.filter(
    (a) => a.build_family === 'app_bundle' && a.lifecycle_status === 'current' && (a.file_count || 0) > 0,
  );
  // Only the terminal passed state proves validation; pending and unknown do not.
  const validated = accepted.filter((a) => a.app_validation_status === 'passed');

  const inventory = `bundles=${bundles.length} artifacts=${artifacts.length} `
    + `accepted=${accepted.length} validated=${validated.length}`;

  if (failedIn) {
    return { ok: false, outcome: 'WORKFLOW_FAILED', summary: `WORKFLOW FAILED in ${failedIn}`,
      notes: [inventory, 'the backend log holds the reason; the UI only says workflow_failed'],
      bundles, artifacts };
  }
  if (!bundles.length) {
    return { ok: false, outcome: 'NO_BUNDLE', summary: `NO BUNDLE - none of our ${ours.length} chat(s) produced files`,
      notes: [inventory], bundles, artifacts };
  }
  if (!accepted.length) {
    return { ok: false, outcome: 'NO_ACCEPTED_ARTIFACT',
      summary: `NO ACCEPTED ARTIFACT - ${bundles.length} bundle(s) but no ArtifactVersion at lifecycle_status='current'`,
      notes: [inventory, 'files were written; nothing was promoted to the accepted version'],
      bundles, artifacts };
  }
  if (!validated.length) {
    return { ok: false, outcome: 'NOT_VALIDATED',
      summary: `NOT VALIDATED - accepted artifact(s) ${accepted.map((a) => a.artifact_version_id).join(', ')} carry no validation result`,
      notes: [inventory,
        `app_validation_status: ${accepted.map((a) => String(a.app_validation_status)).join(', ')}`,
        'an unvalidated build is not a proven one, however complete it looks'],
      bundles, artifacts };
  }
  return { ok: true, outcome: 'VALIDATED_ARTIFACT',
    summary: `VALIDATED ARTIFACT - ${validated.map((a) => `${a.artifact_version_id} (${a.app_validation_status}, ${a.file_count} files)`).join('; ')}`,
    notes: [inventory], bundles, artifacts };
};

const log = [];
function note(stage, detail) {
  const line = `[${new Date().toISOString()}] ${stage}: ${detail}`;
  console.log(`TV ${line}`);
  log.push(line);
  fs.writeFileSync(path.join(OUT, 'traversal.log'), log.join('\n'), 'utf8');
}

const IDEA = notesMode
  ? 'A simple personal notes app. I can add a note with a title and body and see a list of my notes with their creation dates. Version one needs no editing, deleting, payments, external integrations, or AI features.'
  : 'A tool-lending library for a neighbourhood association. Members browse a catalogue of tools, request to borrow one for a date range, an admin approves or declines, and the app tracks due dates and flags overdue items.';

// Substantive and non-repeating: a vague human is a different test, and an
// interview that receives "looks good" five times cannot converge.
// Each answer must carry information. Content-free affirmations ("confirmed",
// "go ahead") are what let an interview drift: the agent proposes, the harness
// agrees, and the concept walks away from the stated idea. A short decisive set
// converges; a long agreeable one trips the convergence guard.
const ANSWERS = notesMode ? [
  'A single user adds notes with a title and body, then sees their notes in a list with creation dates.',
  'Keep version one small: add notes and view the notes list. No editing, deletion, payments or integrations.',
  'Use a clean light theme with a calm blue accent and readable text.',
  'That matches the notes app. Please proceed with this scope.',
] : [
  'Members are neighbours who sign in with email. Admins are two volunteer coordinators.',
  'Start with the catalogue and the borrow-request approval flow. Reminders can come second.',
  'Due-date tracking with an overdue list for admins is essential for version one.',
  'Free for members, no payments. No AI workflows — ordinary modules and pages cover it.',
  'Light mode, soft teal #2F9C95 primary with a warm sand accent, clean humanist sans, rounded corners.',
  'That matches the tool-lending library I described. Please proceed.',
  'Yes — catalogue, borrow requests with admin approval, due dates and an overdue list. Nothing beyond that for version one.',
  'Correct, that is the whole scope. Please produce the concept blueprint for it.',
  'Confirmed: a tool-lending library, no social features, no payments. Go ahead.',
  'Yes, that is the app. Proceed to the next stage.',
];

// Each stage of the build sequence runs its own interview, so a finite list
// starves partway through the journey. These cycle: because every line restates
// the scope rather than merely agreeing, repeating them cannot drift the concept
// the way bare assent did.
const ANCHORS = notesMode ? [
  'Yes, proceed with the small personal notes app: add a title and body and view the list with dates.',
  'Keep the scope to creating and viewing notes; no editing, deletion, payments or AI features.',
] : [
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

test('traversal: idea to accepted, validated artifact', async ({ page }) => {
  recordProvenance();
  const baseline = bundleState();
  if (baseline === null) {
    // Returning here used to leave the run green. A run measured by a broken
    // probe proves nothing, and "proves nothing" is not a pass.
    note('RESULT', 'ABORTED - cannot read run evidence');
    throw new Error('traversal aborted: the evidence probe could not be run; see EVIDENCE_PROBE_FAILED');
  }
  note('BASELINE', `${(baseline.bundles || []).length} bundle(s), ${(baseline.artifacts || []).length} artifact(s) already recorded`);

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

  // The current app_type_selector registers the target before resolving the
  // journey. Its Greenfield card exposes the Start Build action; a generic
  // LauncherScreen fallback cannot register a target and must not be clicked.
  const startBuild = page.getByRole('button', { name: /start build/i }).first();
  let ready = await startBuild.waitFor({ state: 'visible', timeout: 45000 })
    .then(() => true).catch(() => false);
  if (!ready) {
    note('RETRY', 'no Start Build yet; reloading');
    await page.reload();
    ready = await startBuild.waitFor({ state: 'visible', timeout: 45000 })
      .then(() => true).catch(() => false);
  }
  await page.screenshot({ path: path.join(OUT, '00-create.png'), fullPage: true }).catch(() => {});
  if (!ready) {
    const genericChooserVisible = await page.getByRole('button', { name: /Greenfield App/i })
      .first().isVisible().catch(() => false);
    const reason = genericChooserVisible
      ? 'the factory AppTypeSelector did not load; a registered build target was not created'
      : 'no Start Build control was available';
    const verdict = makeBlockedEntryVerdict(reason);
    note('RESULT', verdict.summary);
    fs.writeFileSync(path.join(OUT, 'verdict.json'),
      JSON.stringify({ ...verdict, provenance, chats: [], workflows: [] }, null, 2), 'utf8');
    throw new Error(verdict.summary);
  }
  await startBuild.click().catch(() => {});
  note('CHOSE', 'greenfield via app_type_selector');
  await page.waitForTimeout(8000);

  const seen = new Set();
  let bundleNoted = false;
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

    // Files appear while AppGenerator is still running its tasks and recovery,
    // so the first files are progress, not an end. The run ends when the build
    // chat settles as failed or our app bundle is accepted (2026-11-02, #1005).
    const mine = myBundle();
    if (mine && !bundleNoted) {
      bundleNoted = true;
      note('BUNDLE', `${mine.file_count} files for OUR chat ${String(mine.chat_id).slice(0, 8)}…`);
      await page.screenshot({ path: path.join(OUT, '90-bundle.png'), fullPage: true }).catch(() => {});
    }
    if (mine && Number(mine.status) === WORKFLOW_FAILED_STATUS) {
      failedIn = String(mine.workflow || cur);
      note('WORKFLOW_FAILED', `${failedIn} settled as failed`);
      break;
    }
    const acceptedApp = myAcceptedAppBundle();
    if (acceptedApp) {
      note('ACCEPTED', `${acceptedApp.artifact_version_id} (${acceptedApp.file_count} files)`);
      await page.screenshot({ path: path.join(OUT, '95-accepted.png'), fullPage: true }).catch(() => {});
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
  note('WORKFLOWS_SEEN', [...seen].join(' -> '));
  note('CHATS', [...myChats].join(', ') || '(none)');
  note('FINAL_URL', page.url());
  const evidence = runProbe();
  const generation = judge({ evidence, blocked, failedIn, chats: myChats });
  const exerciseNotes = notesMode ? async (journey) => {
    const acceptedArtifact = myAcceptedAppBundle();
    journey.accepted_artifact = acceptedArtifact?.artifact_version_id || null;
    if (!acceptedArtifact) throw new Error('Accepted artifact evidence is no longer available');
    const firstTitle = 'Cobalt acceptance note';
    const secondTitle = 'Amber acceptance note';
    const previewButton = page.getByRole('button', { name: 'Start draft preview', exact: true }).first();
    await previewButton.waitFor({ state: 'visible', timeout: 300000 });
    await previewButton.click();
    const frameElement = page.locator('iframe[title="Draft app preview"]').first();
    await frameElement.waitFor({ state: 'visible', timeout: 480000 });
    let frame = await frameElement.contentFrame();
    await frame.locator('body').waitFor({ state: 'visible', timeout: 180000 });
    journey.preview_opened = true;
    await createNote(frame, 'Baseline acceptance note', 'A synthetic note created before refinement.');
    journey.baseline_note_created = true;

    const request = page.getByRole('textbox', { name: 'App change request', exact: true }).first();
    await request.fill('Add a visible search field to the notes list that filters notes by title as the user types. Preserve creating and viewing notes. Do not add payments, integrations, or an AI workflow.');
    await page.getByRole('button', { name: 'Apply change', exact: true }).click();
    journey.refinement_submitted = true;
    const result = page.getByRole('status', { name: 'Refinement result', exact: true }).first();
    const refinementDeadline = Date.now() + 20 * 60 * 1000;
    let routeChosen = false;
    while (Date.now() < refinementDeadline) {
      if (await result.getByText('Draft validated and saved for review.', { exact: true }).isVisible().catch(() => false)) break;
      if (await result.isVisible().catch(() => false)) {
        const message = await result.innerText();
        if (/validation failed|refinement failed|not eligible/i.test(message)) throw new Error(message.slice(0, 300));
      }
      const routing = page.getByText('Build Routing', { exact: true }).first();
      if (!routeChosen && await routing.isVisible().catch(() => false)) {
        const card = routing.locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
        const choices = [];
        for (const action of await card.getByRole('button').all()) {
          const label = (await action.innerText().catch(() => '')).trim();
          if (/recommended|apply proposed scope/i.test(label) && !/cancel|reject|back/i.test(label)
              && await action.isVisible().catch(() => false) && await action.isEnabled().catch(() => false)) choices.push(action);
        }
        if (choices.length !== 1) throw new Error('Refinement requires an unrecognized or ambiguous routing decision');
        await choices[0].click();
        routeChosen = true;
      }
      await page.waitForTimeout(3000);
    }
    await result.getByText('Draft validated and saved for review.', { exact: true }).waitFor({ state: 'visible', timeout: 1000 });
    await result.getByText('Refinement details', { exact: true }).click();
    const detail = await result.innerText();
    const draftId = validatedDraftId(detail, journey.accepted_artifact);
    requirePassedDraft(bundleState()?.artifacts, draftId, acceptedArtifact.app_id);
    journey.refinement_draft_id = draftId;
    await page.getByRole('button', { name: 'Update preview', exact: true }).click();
    await page.getByTitle(draftId, { exact: true }).waitFor({ state: 'visible', timeout: 480000 });
    await frameElement.waitFor({ state: 'visible', timeout: 180000 });
    frame = await frameElement.contentFrame();
    // Artifact previews own separate disposable databases; do not claim that a
    // baseline preview record survives the new preview's database lifecycle.
    journey.cross_version_data_continuity = 'not_proven_separate_preview_databases';
    await createNote(frame, firstTitle, 'A synthetic note for title-search acceptance.');
    await createNote(frame, secondTitle, 'A second synthetic note must return after clearing search.');
    await verifyTitleSearch(frame, firstTitle, secondTitle, 'Cobalt');
    journey.refined_create_list_and_search = true;
    await page.screenshot({ path: path.join(OUT, '96-refined-notes.png'), fullPage: true }).catch(() => {});
    note('REFINEMENT_VERIFIED', `draft ${draftId}: create, list and title filtering work in the refined preview`);
  } : null;
  const verdict = await finishTraversal(path.join(OUT, 'verdict.json'), generation,
    { provenance, chats: [...myChats], workflows: [...seen] }, exerciseNotes);
  note('FINISHED_AT', new Date().toISOString());
  note('RESULT', verdict.summary);
  for (const line of verdict.notes) note('NOTE', line);
  if (!verdict.ok) throw new Error(verdict.summary);
});
