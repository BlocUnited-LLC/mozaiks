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
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

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
  for (const status of [null, undefined, 'skipped', 'failed', 'error']) {
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
