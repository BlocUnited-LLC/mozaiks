/**
 * A traversal that did not produce a validated artifact must fail.
 *
 * The harness used to `note('RESULT', ...)` and return. Every run exited
 * `1 passed` -- including BLOCKED, WORKFLOW FAILED and NO BUNDLE -- so four
 * consecutive failed traversals were recorded as four passing tests. A
 * Playwright exit code meant "the browser script finished", not "the product
 * worked".
 *
 * `judge()` is the single place that decides, so this pins its contract without
 * needing a live Studio, a funded wallet, or real model calls.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { createNote, finishTraversal, prepareTraversalOutput, requirePassedDraft, validatedDraftId, verifyTitleSearch } from '../live-traversal/notes-journey.js';
import { isWorkflowReplyRequested } from '../live-traversal/reply-policy.js';

test('traversal waits while working and answers an explicit reply request', async (t) => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.setContent(`
    <div role="status" aria-label="Assistant activity">Working on this step…</div>
    <div role="group" aria-label="Chat status"><span>Working</span><span>Processing your request</span></div>
    <input placeholder="Message the agent" />
  `);
  const composer = page.getByPlaceholder('Message the agent');
  assert.equal(await composer.isEditable(), true);
  for (let i = 0; i < 12; i += 1) {
    assert.equal(await isWorkflowReplyRequested(page), false);
  }
  await page.getByRole('group', { name: 'Chat status' }).evaluate((el) => {
    el.innerHTML = '<span>Waiting for you</span><span>Reply to continue</span>';
  });
  assert.equal(await isWorkflowReplyRequested(page), true);
  await composer.fill('A simple notes app');
  assert.equal(await composer.inputValue(), 'A simple notes app');
  await page.getByRole('group', { name: 'Chat status' }).evaluate((el) => {
    el.innerHTML = '<span>Waiting for you</span><span>Choose the next step</span>';
  });
  assert.equal(await isWorkflowReplyRequested(page), false);
});

test('notes output uses a new owned directory and preserves prior receipts', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'traversal-output-'));
  const prior = path.join(root, 'artifacts');
  await fs.mkdir(prior);
  await fs.writeFile(path.join(prior, 'verdict.json'), 'old receipt');
  const run = path.join(root, 'owned-run');
  assert.equal(prepareTraversalOutput(prior, true, run), run);
  await fs.writeFile(path.join(run, 'verdict.json'), 'new receipt');
  assert.equal(await fs.readFile(path.join(prior, 'verdict.json'), 'utf8'), 'old receipt');
  assert.throws(() => prepareTraversalOutput(prior, true, run), /EEXIST/);
  assert.throws(() => prepareTraversalOutput(prior, true, ''), /requires an absolute/);
  assert.equal(prepareTraversalOutput(prior, false, run), prior);
});

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const specPath = path.join(shell, 'live-traversal/traversal.spec.js');

const loadJudge = async () => {
  const source = await fs.readFile(specPath, 'utf8');
  const start = source.indexOf('const judge = ');
  assert.notEqual(start, -1, 'could not locate judge()');
  const endMarker = '\n};';
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, 'could not locate the end of judge()');
  const ctx = { Set, Array, String, Object, JSON };
  vm.runInNewContext(`${source.slice(start, end + endMarker.length)}\nglobalThis.__judge = judge;`, ctx);
  return ctx.__judge;
};

const loadBlockedEntryVerdict = async () => {
  const source = await fs.readFile(specPath, 'utf8');
  const start = source.indexOf('const makeBlockedEntryVerdict = ');
  assert.notEqual(start, -1, 'could not locate makeBlockedEntryVerdict()');
  const endMarker = '\n});';
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, 'could not locate the end of makeBlockedEntryVerdict()');
  const ctx = { String };
  vm.runInNewContext(
    `${source.slice(start, end + endMarker.length)}\nglobalThis.__blocked = makeBlockedEntryVerdict;`,
    ctx,
  );
  return ctx.__blocked;
};

const chats = new Set(['chat-a']);
const bundle = { chat_id: 'chat-a', workflow: 'AppGenerator', file_count: 12 };
const artifact = (over = {}) => ({
  artifact_version_id: 'av_1', source_chat_id: 'chat-a', lifecycle_status: 'current',
  build_family: 'app_bundle', app_validation_status: 'passed', file_count: 12, ...over,
});

test('a blocked run fails', async () => {
  const judge = await loadJudge();
  const v = judge({ evidence: null, blocked: 'AI token balance exhausted.', failedIn: null, chats });
  assert.equal(v.ok, false);
  assert.equal(v.outcome, 'BLOCKED');
});

test('an unmeasured run fails rather than passing quietly', async () => {
  const judge = await loadJudge();
  const v = judge({ evidence: null, blocked: null, failedIn: null, chats });
  assert.equal(v.ok, false);
  assert.equal(v.outcome, 'UNKNOWN');
});

test('a failed workflow fails even when files exist', async () => {
  const judge = await loadJudge();
  const v = judge({
    evidence: { bundles: [bundle], artifacts: [artifact()] },
    blocked: null, failedIn: 'AppGenerator', chats,
  });
  assert.equal(v.ok, false);
  assert.equal(v.outcome, 'WORKFLOW_FAILED');
});

test('no bundle fails', async () => {
  const judge = await loadJudge();
  const v = judge({ evidence: { bundles: [], artifacts: [] }, blocked: null, failedIn: null, chats });
  assert.equal(v.ok, false);
  assert.equal(v.outcome, 'NO_BUNDLE');
});

test('another run\'s bundle cannot pass this one', async () => {
  const judge = await loadJudge();
  const v = judge({
    evidence: {
      bundles: [{ chat_id: 'someone-else', workflow: 'AppGenerator', file_count: 20 }],
      artifacts: [artifact({ source_chat_id: 'someone-else' })],
    },
    blocked: null, failedIn: null, chats,
  });
  assert.equal(v.ok, false, 'a concurrent agent\'s build is not our evidence');
  assert.equal(v.outcome, 'NO_BUNDLE');
});

test('files without an accepted artifact fail', async () => {
  const judge = await loadJudge();
  const v = judge({
    evidence: { bundles: [bundle], artifacts: [artifact({ lifecycle_status: 'draft' })] },
    blocked: null, failedIn: null, chats,
  });
  assert.equal(v.ok, false);
  assert.equal(v.outcome, 'NO_ACCEPTED_ARTIFACT');
});

test('an accepted artifact with no validation result fails', async () => {
  const judge = await loadJudge();
  for (const status of [null, undefined, 'skipped', 'failed', 'error', 'pending', 'running', 'unknown', 'PASSED']) {
    const v = judge({
      evidence: { bundles: [bundle], artifacts: [artifact({ app_validation_status: status })] },
      blocked: null, failedIn: null, chats,
    });
    assert.equal(v.ok, false, `app_validation_status=${status} must not pass`);
    assert.equal(v.outcome, 'NOT_VALIDATED');
  }
});

test('only an accepted, validated artifact passes', async () => {
  const judge = await loadJudge();
  const v = judge({
    evidence: { bundles: [bundle], artifacts: [artifact()] },
    blocked: null, failedIn: null, chats,
  });
  assert.equal(v.ok, true);
  assert.equal(v.outcome, 'VALIDATED_ARTIFACT');
  assert.match(v.summary, /av_1/);
});

test('accepted concept, theme, and design-docs versions are not the build', async () => {
  // Live traversal 2026-11-02 (#1005): these are accepted early, before the app
  // bundle exists, and never carry an app validation.
  const judge = await loadJudge();
  const upstream = ['concept', 'theme_capture', 'design_docs'].map((family, index) => artifact({
    artifact_version_id: `av_up_${index}`, build_family: family, app_validation_status: null,
  }));
  const v = judge({
    evidence: { bundles: [bundle], artifacts: upstream },
    blocked: null, failedIn: null, chats,
  });
  assert.equal(v.ok, false);
  assert.equal(v.outcome, 'NO_ACCEPTED_ARTIFACT');
});

test('the spec throws on a failing verdict', async () => {
  // The judge is useless if the caller still exits 0.
  const source = await fs.readFile(specPath, 'utf8');
  assert.match(source, /if \(!verdict\.ok\) throw new Error\(verdict\.summary\);/);
});

test('the factory app-type selector starts Greenfield and missing controls fail', async () => {
  const source = await fs.readFile(specPath, 'utf8');
  assert.match(source, /name: \/start build\/i/, 'the factory AppTypeSelector owns the target-registration action');
  const branchMatch = source.match(/if \(!ready\) \{\s+const genericChooserVisible[\s\S]+?throw new Error\(verdict\.summary\);\s+\}/);
  assert.ok(branchMatch, 'missing entry controls must have an explicit failing verdict branch');
  const branch = branchMatch[0];
  assert.match(branch, /makeBlockedEntryVerdict/);
  assert.match(branch, /verdict\.json/);
  assert.match(branch, /throw new Error\(verdict\.summary\)/);
  assert.doesNotMatch(branch, /\breturn\s*;/, 'a blocked traversal must not exit successfully');

  const makeBlockedEntryVerdict = await loadBlockedEntryVerdict();
  const verdict = makeBlockedEntryVerdict('no entry control');
  assert.equal(verdict.ok, false);
  assert.equal(verdict.outcome, 'BLOCKED');
  assert.match(verdict.summary, /no entry control/);
});

test('the interpreter path is not hardcoded', async () => {
  const source = await fs.readFile(specPath, 'utf8');
  assert.doesNotMatch(source, /['"][a-zA-Z]:\//, 'an absolute local path pins this to one machine');
  assert.match(source, /MOZAIKS_TRAVERSAL_PYTHON/);
});

test('a run records what produced it', async () => {
  const source = await fs.readFile(specPath, 'utf8');
  for (const field of ['package_path', 'package_version', 'oss_sha', 'oss_branch', 'started_at']) {
    assert.match(source, new RegExp(field), `provenance must record ${field}`);
  }
  assert.match(source, /verdict\.json/, 'the structured verdict must be written out');
});

test('refinement requires a distinct validated draft identity', () => {
  assert.equal(validatedDraftId('Status: validated • Draft av_child', 'av_parent'), 'av_child');
  assert.throws(() => validatedDraftId('Status: planned • Draft av_child', 'av_parent'));
  assert.throws(() => validatedDraftId('Draft validated and saved for review.', 'av_parent'));
  assert.throws(() => validatedDraftId('Status: validated • Draft av_parent', 'av_parent'));
  assert.throws(() => validatedDraftId('Status: validated • Draft av_child', null));
});

test('post-generation failure persists a failed overall receipt', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'mozaiks-traversal-verdict-'));
  const receipt = path.join(directory, 'verdict.json');
  try {
    const generation = { ok: true, outcome: 'VALIDATED_ARTIFACT', summary: 'generation passed', notes: [] };
    const verdict = await finishTraversal(receipt, generation, { chats: ['synthetic'] }, async (journey) => {
      const inProgress = JSON.parse(await fs.readFile(receipt, 'utf8'));
      assert.equal(inProgress.ok, false, 'an interrupted journey must not leave a passing receipt');
      journey.preview_opened = true;
      throw new Error('synthetic refinement failure');
    });
    const persisted = JSON.parse(await fs.readFile(receipt, 'utf8'));
    assert.equal(verdict.ok, false);
    assert.equal(persisted.ok, false);
    assert.equal(persisted.outcome, 'NOTES_JOURNEY_FAILED');
    assert.equal(persisted.generation_verdict.ok, true);
    assert.match(persisted.journey.error, /synthetic refinement failure/);
  } finally {
    await fs.unlink(receipt).catch(() => {});
    await fs.rmdir(directory);
  }
});

test('generation-only mode keeps the generation verdict', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'mozaiks-generation-verdict-'));
  const receipt = path.join(directory, 'verdict.json');
  try {
    const generation = { ok: true, outcome: 'VALIDATED_ARTIFACT', summary: 'generation passed', notes: [] };
    const verdict = await finishTraversal(receipt, generation, {});
    assert.equal(verdict.outcome, 'VALIDATED_ARTIFACT');
    assert.equal(JSON.parse(await fs.readFile(receipt, 'utf8')).ok, true);
  } finally {
    await fs.unlink(receipt).catch(() => {});
    await fs.rmdir(directory);
  }
});

test('a failure after all journey flags still persists a failing receipt', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'mozaiks-completed-flags-'));
  const receipt = path.join(directory, 'verdict.json');
  try {
    const generation = { ok: true, outcome: 'VALIDATED_ARTIFACT', summary: 'generation passed', notes: [] };
    const verdict = await finishTraversal(receipt, generation, {}, async (journey) => {
      Object.assign(journey, {
        accepted_artifact: 'parent', preview_opened: true, baseline_note_created: true,
        refinement_submitted: true, refinement_draft_id: 'child', refined_create_list_and_search: true,
      });
      throw new Error('synthetic failure after completion flags');
    });
    const persisted = JSON.parse(await fs.readFile(receipt, 'utf8'));
    assert.equal(verdict.ok, false);
    assert.equal(persisted.ok, false);
    assert.equal(persisted.outcome, 'NOTES_JOURNEY_FAILED');
    assert.match(persisted.journey.error, /synthetic failure after completion flags/);
  } finally {
    await fs.unlink(receipt).catch(() => {});
    await fs.rmdir(directory);
  }
});

test('a returned journey with every required check passes', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'mozaiks-successful-journey-'));
  const receipt = path.join(directory, 'verdict.json');
  try {
    const generation = { ok: true, outcome: 'VALIDATED_ARTIFACT', summary: 'generation passed', notes: [] };
    const verdict = await finishTraversal(receipt, generation, {}, async (journey) => {
      Object.assign(journey, {
        accepted_artifact: 'parent', preview_opened: true, baseline_note_created: true,
        refinement_submitted: true, refinement_draft_id: 'child', refined_create_list_and_search: true,
      });
    });
    assert.equal(verdict.ok, true);
    assert.equal(JSON.parse(await fs.readFile(receipt, 'utf8')).outcome, 'VALIDATED_NOTES_JOURNEY');
  } finally {
    await fs.unlink(receipt).catch(() => {});
    await fs.rmdir(directory);
  }
});

test('refined draft evidence must match identity, app, family and passed status', () => {
  const artifact = { artifact_version_id: 'child', app_id: 'app', build_family: 'app_bundle', app_validation_status: 'passed' };
  assert.equal(requirePassedDraft([artifact], 'child', 'app'), artifact);
  assert.throws(() => requirePassedDraft([artifact], 'child', null));
  for (const candidate of [undefined, [], [{ ...artifact, app_validation_status: 'pending' }],
    [{ ...artifact, app_id: 'other' }], [{ ...artifact, build_family: 'concept' }],
    [{ ...artifact, artifact_version_id: 'different' }]]) {
    assert.throws(() => requirePassedDraft(candidate, 'child', 'app'));
  }
});

test('an always-visible Add Note submits exactly once per creation', async (t) => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'] });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => route.abort());
  await page.setContent(`
    <label>Title <input id="title"></label><label>Body <input id="body"></label>
    <button id="add">Add Note</button><ul id="notes"></ul>
    <script>
      document.querySelector('#add').onclick = () => {
        const title = document.querySelector('#title').value;
        if (!title) return;
        const item = document.createElement('li');
        item.textContent = title;
        document.querySelector('#notes').append(item);
      };
    </script>`);
  for (const title of ['First synthetic note', 'Second synthetic note']) {
    const before = await page.locator('#notes li').count();
    await createNote(page, title, 'Synthetic body');
    assert.equal(await page.locator('#notes li').count(), before + 1);
  }
});

test('a New Note opener still opens a hidden form', async (t) => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'] });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => route.abort());
  await page.setContent(`
    <button id="open">New Note</button>
    <div id="form" hidden><label>Title <input id="title"></label><label>Body <input id="body"></label>
      <button id="save">Save Note</button></div><ul id="notes"></ul>
    <script>
      document.querySelector('#open').onclick = () => { document.querySelector('#form').hidden = false; };
      document.querySelector('#save').onclick = () => {
        const item = document.createElement('li');
        item.textContent = document.querySelector('#title').value;
        document.querySelector('#notes').append(item);
      };
    </script>`);
  await createNote(page, 'Opened synthetic note', 'Synthetic body');
  assert.equal(await page.locator('#notes li').count(), 1);
});

test('notes traversal ignores hidden mobile copies before visible desktop rows', async (t) => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'] });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => route.abort());
  await page.setContent(`
    <label>Title <input id="title"></label><label>Body <input id="body"></label>
    <button id="save">Save Note</button><label>Search <input id="search" type="search"></label>
    <div id="mobile" hidden></div><div id="desktop"></div>
    <script>
      document.querySelector('#save').onclick = () => {
        for (const id of ['mobile', 'desktop']) {
          const row = document.createElement('div');
          row.textContent = document.querySelector('#title').value;
          document.querySelector('#' + id).append(row);
        }
      };
      document.querySelector('#search').oninput = event => {
        for (const row of document.querySelectorAll('#desktop > div')) {
          row.hidden = !row.textContent.toLowerCase().includes(event.target.value.toLowerCase());
        }
      };
    </script>`);
  await createNote(page, 'Cobalt desktop note', 'Synthetic body', 1000);
  await createNote(page, 'Amber desktop note', 'Synthetic body', 1000);
  await verifyTitleSearch(page, 'Cobalt desktop note', 'Amber desktop note', 'Cobalt', 1000);
});

test('title search finds visible rows after hidden copies', async (t) => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'] });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => route.abort());
  await page.setContent(`
    <label>Search <input id="search" type="search"></label>
    <div hidden><div>Cobalt note</div><div>Amber note</div></div>
    <div id="desktop"><div>Cobalt note</div><div>Amber note</div></div>
    <script>
      document.querySelector('#search').oninput = event => {
        for (const row of document.querySelectorAll('#desktop > div')) {
          row.hidden = !row.textContent.toLowerCase().includes(event.target.value.toLowerCase());
        }
      };
    </script>`);
  await verifyTitleSearch(page, 'Cobalt note', 'Amber note', 'Cobalt', 1000);
});

// Synthetic browser fixture proves these assertions, not app generation.
test('notes journey checks creation, filtering and restoration after clearing', async (t) => {
  const { chromium } = await import('@playwright/test');
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'] });
  const page = await browser.newPage();
  await page.route('**/*', route => route.abort());
  try {
    await page.setContent(`
      <label>Title <input id="title"></label><label>Body <input id="body"></label>
      <button id="save">Save note</button><label>Search <input id="search" type="search"></label>
      <ul id="notes"></ul>
      <script>
        document.querySelector('#save').onclick = () => {
          const li = document.createElement('li');
          li.textContent = document.querySelector('#title').value;
          li.dataset.body = document.querySelector('#body').value;
          document.querySelector('#notes').append(li);
        };
        window.filterMode = 'works';
        document.querySelector('#search').oninput = event => {
          for (const item of document.querySelectorAll('#notes li')) {
            if (window.filterMode === 'works' || window.filterMode === 'title_and_body' || (window.filterMode === 'sticky' && event.target.value)) {
              const searchable = window.filterMode === 'title_and_body' ? item.textContent + item.dataset.body : item.textContent;
              item.hidden = !searchable.toLowerCase().includes(event.target.value.toLowerCase());
            }
          }
        };
      </script>`);
    await createNote(page, 'Cobalt acceptance note', 'Synthetic body');
    await createNote(page, 'Amber acceptance note', 'Cobalt appears only in the body');
    await verifyTitleSearch(page, 'Cobalt acceptance note', 'Amber acceptance note', 'Cobalt', 500);

    await t.test('matching body alone must not count as a title match', async () => {
      await page.evaluate(() => { window.filterMode = 'title_and_body'; });
      await assert.rejects(verifyTitleSearch(page, 'Cobalt acceptance note', 'Amber acceptance note', 'Cobalt', 250));
      await page.getByRole('searchbox').fill('');
      await page.evaluate(() => { window.filterMode = 'works'; });
    });

    await t.test('a field that does not filter fails', async () => {
      await page.evaluate(() => { window.filterMode = 'broken'; });
      await assert.rejects(verifyTitleSearch(page, 'Cobalt acceptance note', 'Amber acceptance note', 'Cobalt', 250));
      await page.getByRole('searchbox').fill('');
      await page.evaluate(() => { window.filterMode = 'works'; });
    });
    await t.test('clearing search must restore both notes', async () => {
      await page.evaluate(() => { window.filterMode = 'sticky'; });
      await assert.rejects(verifyTitleSearch(page, 'Cobalt acceptance note', 'Amber acceptance note', 'Cobalt', 250));
      await page.evaluate(() => { window.filterMode = 'works'; });
      await page.getByRole('searchbox').fill('Cobalt');
      await page.getByRole('searchbox').fill('');
    });
    await t.test('a missing created note fails', async () => {
      await page.getByText('Amber acceptance note').evaluate(node => node.remove());
      await assert.rejects(verifyTitleSearch(page, 'Cobalt acceptance note', 'Amber acceptance note', 'Cobalt', 250));
    });
  } finally {
    await page.close();
    await browser.close();
  }
});
