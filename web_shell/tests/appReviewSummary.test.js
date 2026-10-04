import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = await build({
  entryPoints: [path.join(shell, '../factory_app/workflows/AppReview/ui/AppReview/AppReviewSummary.jsx')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], nodePaths: [path.join(shell, 'node_modules')],
  plugins: [{ name: 'isolated-review', setup(builder) {
    builder.onResolve({ filter: /@mozaiks\/chat-ui\/ui|studioApi\.js$/ }, args => ({ path: args.path, namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({
      loader: 'jsx', resolveDir: shell,
      contents: args.path.endsWith('studioApi.js')
        ? 'export const studioFetch = () => { throw Error("No live API allowed"); };'
        : `export const Panel = ({children}) => <section>{children}</section>;
           export const StatusPill = ({children}) => <span>{children}</span>;
           export const Button = ({children, disabled}) => <button disabled={disabled}>{children}</button>;
           export const Metric = ({label, value}) => <p>{label}: {value}</p>;`,
    }));
  } }],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const render = payload => renderToStaticMarkup(createElement(module.exports.default, { payload }));

test('missing review evidence is visible as Missing, never Skipped', () => {
  const html = render({ can_promote: false });
  for (const label of ['Bundle acceptance', 'Build validation', 'Integration checks', 'Security readiness']) {
    assert.match(html, new RegExp(`${label}</span><span>Missing</span>`));
  }
  assert.doesNotMatch(html, /Skipped/);
  assert.match(html, /<button disabled=""/);
});

test('explicit skipped validation blocks activation even with an inconsistent readiness flag', () => {
  const html = render({
    app_validation_status: 'skipped', app_validation_strategy_used: 'skip',
    app_bundle_acceptance_status: 'passed', integration_tests_passed: true,
    security_readiness_summary: { status: 'passed', finding_count: 0, persisted: false, success: true },
    can_promote: true, artifact_version_id: 'artifact', build_registry_id: 'owned-build',
  });
  assert.match(html, /Build validation<\/span><span>Skipped<\/span>/);
  for (const label of ['Bundle acceptance', 'Integration checks', 'Security readiness']) {
    assert.match(html, new RegExp(`${label}</span><span>Passed</span>`));
  }
  assert.doesNotMatch(html, /Missing/);
  assert.match(html, /<button disabled=""/);
  assert.match(html, /This draft needs attention/);
});

test('review needs an explicit positive readiness decision and does not claim deployment', () => {
  const payload = {
    app_validation_status:'passed', app_bundle_acceptance_status:'passed', integration_tests_passed:true,
    artifact_version_id:'artifact', build_registry_id:'owned-build',
  };
  assert.match(render(payload), /<button disabled=""/);
  const ready = render({...payload, can_promote:true});
  assert.doesNotMatch(ready, /disabled=""|your build is live/);
  assert.match(ready, /Ready for your decision/);
  assert.match(ready, /<details[^>]*>/);
  assert.doesNotMatch(ready, /<details[^>]*open/);
});

test('failed checks stay Failed and promotion remains disabled', () => {
  const html = render({
    app_validation_status: 'failed', app_bundle_acceptance_status: 'failed', integration_tests_passed: false,
    can_promote: false, artifact_version_id: 'artifact', build_registry_id: 'owned-build',
  });
  for (const label of ['Bundle acceptance', 'Build validation', 'Integration checks']) {
    assert.match(html, new RegExp(`${label}</span><span>Failed</span>`));
  }
  assert.match(html, /<button disabled=""/);
});

test('workbench reviews saved candidates without rerunning coding or replacing an unvalidated baseline', async (t) => {
  // Render the production component and click its controls. Only transport,
  // Monaco, and sandbox allocation are fixture boundaries; no model calls run.
  const root = path.dirname(shell);
  const stubs = {
    '@mozaiks/chat-ui/hooks/useWorkflowStart.js': `
      import { useState } from 'react';
      export function useWorkflowStart() {
        const [starting, setStarting] = useState(false);
        return { starting, error: null, startWorkflow: async (...args) => {
          setStarting(true);
          try { return await (await fetch('/fixture-trigger', {method:'POST', body:JSON.stringify(args)})).json(); }
          finally { setStarting(false); }
        } };
      }`,
    './useSandbox': `export const useSandbox = () => ({syncAndRestart(){}, stopPreview(){}});`,
    './CodeEditorPane': `export default function Editor({content}) { return <output aria-label="Editor contents">{content}</output>; }`,
    './PreviewPane': `export default function Preview({artifactVersionId}) { return <output aria-label="Preview version">{artifactVersionId}</output>; }`,
    '../../adapters/api.js': `export const authFetch = (...args) => fetch(...args);`,
    '../../../app/admin/pages/studioApi.js': 'export const studioFetch = (...args) => fetch(...args);',
  };
  const fixture = await build({
    stdin: {resolveDir: shell, loader: 'jsx', contents: `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import AppWorkbench from ${JSON.stringify(path.join(root, 'factory_app/workflows/AppGenerator/ui/AppWorkbench.js'))};
      const delivery = new URLSearchParams(location.search).get('delivery') || 'files';
      const payload = {artifact_version_id:'baseline', build_registry_id:'owned-build',
        refinement_result:window.initialRefinement,
        files:delivery==='files' || delivery==='continue' ? [{name:'app.zip'}] : [],
        ...(delivery==='continue' ? {actions:[
          {id:'continue', label:'Continue', approved:true},
          {id:'close', label:'Close'},
          {id:'export_to_github', label:'Export to GitHub'},
        ]} : {}),
        stage:delivery==='confirm' || delivery==='custom-confirm' ? 'confirm' : 'files_ready',
        ...(delivery==='custom-confirm' ? {actions:[
          {id:'download_complete', label:'Download Bundle', approved:true},
          {id:'close', label:'Return to editor'},
        ]} : {}),
        generated_files:{'README.md':'Original contents', 'notes.md':'Original notes'}, app_validation_status:'passed',
        app_validation_strategy_used:'parent-only-strategy',
        app_validation_result:{warnings:['Parent evidence only']}, integration_tests_passed:true,
        integration_test_result:{passed:true, warnings:['Custom page bindings need review.']}};
      createRoot(document.getElementById('root')).render(<AppWorkbench payload={payload}
        onResponse={response => fetch('/fixture-response',{method:'POST',body:JSON.stringify(response)})} />);
    `},
    bundle: true, write: false, jsx: 'automatic', loader: {'.js':'jsx', '.png':'dataurl'}, nodePaths: [path.join(shell, 'node_modules')],
    alias: {react:path.join(shell, 'node_modules/react'), 'react-dom':path.join(shell, 'node_modules/react-dom')},
    plugins: [{name:'workbench-boundaries', setup(builder) {
      builder.onResolve({filter:/.*/}, args => {
        if (Object.hasOwn(stubs, args.path)) return {path:args.path, namespace:'fixture'};
        if (args.path.startsWith('@mozaiks/chat-ui/')) return {path:path.join(root, 'chat-ui/src', args.path.slice('@mozaiks/chat-ui/'.length))};
      });
      builder.onLoad({filter:/.*/, namespace:'fixture'}, args => ({contents:stubs[args.path], loader:'jsx', resolveDir:shell}));
    }}],
  });
  let scenario;
  let accepted = false;
  const requests = [];
  const candidateValidation = () => scenario.missingProof ? {validation_status:"passed"} : ({
    validation_status:scenario.validation, validation_strategy:'local',
    app_bundle_acceptance_result:{status:scenario.validation, passed:scenario.validation==='passed'},
    app_validation_result:{validation_status:scenario.validation, validation_strategy:'local'},
  });
  const triggerResult = () => scenario.decision ? ({
    execution_mode:'harness_decision', change_request_id:'change-1', revision_id:'revision-1',
    harness_decision:{decision_type:'clarify_scope', message:'Confirm these two files.',
      selected_paths:['README.md','notes.md'], actions:[{
        action_id:scenario.decision, action_type:'confirm_scope', label:'Continue with this scope',
      }]},
  }) : ({
    execution_mode:scenario.mode || 'coding_worker',
    ...(scenario.mode === 'surface_regeneration' ? {surface_result:{
      status:{validated:'success', planned:'partial', failed:'failed'}[scenario.status],
      all_files:{'README.md':'Candidate contents'},
      metadata:{...(scenario.saved ? {build_record_id:'candidate'} : {}), validation_result:candidateValidation()},
      surfaces_executed:scenario.status === 'failed' ? [{status:'failed', error:'Required checks failed.'}] : [],
    }} : {coding_worker:{
      status:scenario.status, applied_files:{'README.md':'Candidate contents'},
      validation_result:candidateValidation(),
      metadata:scenario.saved ? {build_record_id:'candidate'} : {},
      plan:{summary:'Update the selected document while preserving its surrounding files.'},
      error:scenario.status === 'failed' ? 'Required checks failed.' : null,
    }}),
    harness_decision:{decision_type:'auto_patch', message:'Inspect the refinement result.',
      actions:scenario.saved ? [{action_id:'review_patch', action_type:'review_patch', label:'Review patch'}] : []},
  });
  const review = id => ({
    lifecycle_status:'draft', validation_status: id === 'baseline' ? 'passed' : scenario.validation,
    review_status: id === 'baseline' ? 'validated' : scenario.status,
    changed_file_count: id === 'baseline' ? 0 : 1,
    coding_summary: id === 'baseline' ? null : 'Update the selected document while preserving its surrounding files.',
    changed_files: id === 'baseline' ? [] : [{path:'README.md', change_type:'modified', diff_preview:'-Original contents\n+Candidate contents'}],
    can_accept: id !== 'baseline' && scenario.status === 'validated' && !accepted,
    can_reject: id !== 'baseline', can_promote:scenario.promotion === true && accepted,
    risk_notes:['A binary asset was omitted from the text diff.'],
    validation_blocker: id !== 'baseline' && scenario.status !== 'validated' ? 'Validation has not passed.' : null,
  });
  const server = http.createServer(async (req, res) => {
    if (req.url === '/fixture.js') {
      res.setHeader('Content-Type','text/javascript'); res.end(fixture.outputFiles[0].text); return;
    }
    if (req.url === '/' || req.url.startsWith('/?')) {
      res.setHeader('Content-Type','text/html');
      res.end(`<div id="root"></div><script>window.initialRefinement=${req.url.includes('initial=true') ? JSON.stringify(triggerResult()) : 'null'}</script><script src="/fixture.js"></script>`); return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    requests.push({url:req.url, method:req.method, body:Buffer.concat(chunks).toString()});
    res.setHeader('Content-Type','application/json');
    if (req.url === '/fixture-response') {res.end('{"accepted":true}'); return;}
    if (req.url === '/fixture-trigger') {
      if (scenario.hold) { scenario.release = () => res.end(JSON.stringify(triggerResult())); return; }
      res.end(JSON.stringify(triggerResult()));
      return;
    }
    const match = req.url.match(/^\/api\/studio\/build\/artifacts\/(baseline|candidate)\/(review|reject|accept|promote)\?build_registry_id=owned-build$/);
    if (!match) { res.statusCode=404; res.end('{"detail":"Unexpected fixture route"}'); return; }
    if (scenario.reviewError && match[1] === 'candidate') {
      res.statusCode=503; res.end('{"detail":"Saved draft review is unavailable."}'); return;
    }
    if (scenario.promotion && match[2] === 'accept') accepted = true;
    const confirmed = {accept:'accepted', reject:'rejected', promote:'promoted'}[match[2]];
    res.end(JSON.stringify({review:review(match[1]), ...(confirmed ? {[confirmed]:true} : {}),
      ...(match[2] === 'promote' ? {restart_required:true} : {})}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => {server.closeAllConnections(); server.close(resolve);}));
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  await t.test('Entire app proposes scope; confirmation sends only displayed paths and binds the pending request', async () => {
    scenario = {decision:'apply_proposed_scope'};
    requests.length = 0;
    const page = await browser.newPage();
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.getByRole('textbox', {name:'App change request'}).fill('Update both documents.');
      await page.getByRole('button', {name:'Apply change',exact:true}).click();
      await expect(page.getByRole('button', {name:'Continue with this scope'})).toBeVisible();
      const initial = JSON.parse(requests.find(r => r.url==='/fixture-trigger').body)[2].trigger_payload;
      assert.deepEqual(initial.coding_request, {});
      await page.getByRole('button', {name:'Split',exact:true}).click();
      await page.getByRole('checkbox', {name:'Limit to selected file'}).check();
      // Changing a UI selection must not change the already displayed proposal.
      await page.getByRole('button', {name:'Continue with this scope'}).click();
      await expect.poll(() => requests.filter(r => r.url==='/fixture-trigger').length).toBe(2);
      const confirmed = JSON.parse(requests.filter(r => r.url==='/fixture-trigger')[1].body)[2].trigger_payload;
      assert.equal(confirmed.change_request_id, 'change-1');
      assert.equal(confirmed.revision_id, 'revision-1');
      assert.deepEqual(confirmed.refinement_request, initial.refinement_request);
      assert.deepEqual(confirmed.coding_request.files, {'README.md':'Original contents', 'notes.md':'Original notes'});
      await page.getByRole('textbox', {name:'App change request'}).fill('A different request.');
      await expect(page.getByRole('button', {name:'Continue with this scope'})).toHaveCount(0);
      assert.equal(requests.filter(r => r.url==='/fixture-trigger').length, 2);
    } finally { await page.close(); }
  });
  await t.test('selected file is explicit and workflow continuation removes inline coding', async () => {
    scenario = {decision:'run_recommended_workflow'};
    requests.length = 0;
    const page = await browser.newPage();
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.getByRole('button', {name:'Split',exact:true}).click();
      await page.getByRole('checkbox', {name:'Limit to selected file'}).check();
      await page.getByRole('textbox', {name:'App change request'}).fill('Update the selected document.');
      await page.getByRole('button', {name:'Apply change',exact:true}).click();
      await expect(page.getByRole('button', {name:'Continue with this scope'})).toBeVisible();
      assert.deepEqual(JSON.parse(requests.find(r=>r.url==='/fixture-trigger').body)[2].trigger_payload.coding_request.files,
        {'README.md':'Original contents'});
      await page.getByRole('button', {name:'Continue with this scope'}).click();
      await expect.poll(() => requests.filter(r=>r.url==='/fixture-trigger').length).toBe(2);
      const payload = JSON.parse(requests.filter(r=>r.url==='/fixture-trigger')[1].body)[2].trigger_payload;
      assert.equal(Object.hasOwn(payload,'coding_request'),false);
      assert.equal(payload.harness_action.action_id,'run_recommended_workflow');
    } finally { await page.close(); }
  });
  await t.test('pending Apply disables review mutations and a scope question preserves completed candidate evidence', async () => {
    scenario = {status:'validated', validation:'passed', saved:true};
    requests.length = 0;
    const page = await browser.newPage();
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}/?initial=true`);
      const panel = page.getByRole('region', {name:'Artifact review'});
      await expect(panel.getByRole('button', {name:'Accept artifact',exact:true})).toBeEnabled();
      scenario.hold = true;
      await page.getByRole('textbox', {name:'App change request'}).fill('Update both documents.');
      await page.getByRole('button', {name:'Apply change',exact:true}).click();
      await expect.poll(() => typeof scenario.release).toBe('function');
      await expect(panel.getByRole('button', {name:'Accept artifact',exact:true})).toBeDisabled();
      await expect(panel.getByRole('button', {name:'Reject artifact',exact:true})).toBeDisabled();
      await expect(page.getByLabel('Preview version')).toHaveText('candidate');
      scenario.decision = 'apply_proposed_scope';
      scenario.release();
      scenario.release = null;
      await expect(page.getByRole('button', {name:'Continue with this scope'})).toBeVisible();
      await expect(panel.getByRole('button', {name:'Accept artifact',exact:true})).toBeEnabled();
      await expect(page.getByRole('status', {name:'Refinement result'})).toContainText('Draft validated and saved for review.');
      await expect(page.getByText('Validation passed', {exact:true})).toBeVisible();
      await expect(page.getByLabel('Preview version')).toHaveText('candidate');
      assert.ok(!requests.some(r=>r.method==='POST' && r.url.includes('/candidate/')));
    } finally { if (scenario.release) scenario.release(); await page.close(); }
  });
  await t.test('preview and changes lead; code and export are opt-in; activation requires its own click', async () => {
    scenario = {status:'validated', validation:'passed', saved:true, promotion:true};
    accepted = false;
    requests.length = 0;
    const page = await browser.newPage({viewport:{width:390, height:844}});
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await expect(page.getByLabel('Preview version')).toHaveText('baseline');
      await expect(page.getByLabel('Editor contents')).toHaveCount(0);
      await expect(page.getByRole('textbox', {name:'App change request'})).toBeVisible();
      await expect(page.getByText('Custom page bindings need review.', {exact:true})).toBeHidden();
      await page.getByText('1 integration warning(s) to review', {exact:true}).click();
      await expect(page.getByText('Custom page bindings need review.', {exact:true})).toBeVisible();
      await expect(page.getByText('Parent evidence only', {exact:true})).toBeHidden();
      await page.getByRole('button', {name:'1 warning(s)',exact:true}).click();
      await expect(page.getByText('Parent evidence only', {exact:true})).toBeVisible();
      await expect(page.getByText('Download and export details', {exact:true})).toBeVisible();
      await expect(page.getByText('app.zip', {exact:true})).toBeHidden();
      assert.ok(!requests.some(r => r.method === 'POST'));
      await page.getByRole('textbox', {name:'App change request'}).fill('Change the README.');
      await page.getByRole('button', {name:'Apply change',exact:true}).click();
      const panel = page.getByRole('region', {name:'Artifact review'});
      await expect(panel.getByRole('button', {name:'Accept artifact',exact:true})).toBeVisible();
      const result = page.getByRole('status', {name:'Refinement result'});
      const summary = 'Update the selected document while preserving its surrounding files.';
      await expect(result.getByText(summary, {exact:true})).toBeHidden();
      await expect(panel.getByText(summary, {exact:true})).toBeHidden();
      await result.getByText('Refinement details', {exact:true}).click();
      await expect(result.getByText(summary, {exact:true})).toBeVisible();
      await panel.getByText('Change summary', {exact:true}).click();
      await expect(panel.getByText(summary, {exact:true})).toBeVisible();
      await expect(panel.getByText('A binary asset was omitted from the text diff.', {exact:true})).toBeHidden();
      await panel.getByText('Review notes (1)', {exact:true}).click();
      await expect(panel.getByText('A binary asset was omitted from the text diff.', {exact:true})).toBeVisible();
      await expect(panel.getByText('-Original contents\n+Candidate contents', {exact:true})).toBeHidden();
      await expect(panel.getByRole('button', {name:'Activate this draft',exact:true})).toHaveCount(0);
      await panel.getByText('Code changes (1)', {exact:true}).click();
      await expect(panel.getByText('-Original contents\n+Candidate contents', {exact:true})).toBeVisible();
      await panel.getByRole('button', {name:'Accept artifact',exact:true}).click();
      await expect(panel.getByRole('button', {name:'Activate this draft',exact:true})).toBeVisible();
      await expect(panel.getByRole('status')).toHaveText('Draft accepted. Activate it when you are ready.');
      await expect(panel.getByText('Review this version before making it the active app.', {exact:true})).toBeVisible();
      assert.ok(!requests.some(r => r.url.includes('/promote?')));
      await panel.getByRole('button', {name:'Activate this draft',exact:true}).click();
      await expect.poll(() => requests.filter(r => r.url.includes('/candidate/promote?')).length).toBe(1);
      await expect(panel.getByRole('status')).toHaveText('Version activated. Restart the app to load this version.');
      assert.equal(requests.filter(r => r.url === '/fixture-trigger').length, 1);
    } finally { accepted = false; await page.close(); }
  });
  await t.test('Continue remains visible while export fields are collapsed and requires acknowledgement', async () => {
    scenario = {status:'planned', validation:'pending', saved:true};
    requests.length = 0;
    const page = await browser.newPage();
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}/?delivery=continue`);
      const proceed = page.getByRole('button', {name:'Continue',exact:true});
      await expect(proceed).toBeVisible();
      await expect(page.getByRole('textbox', {name:'Repository Name'})).toBeHidden();
      await expect(page.getByRole('button', {name:'Export to GitHub',exact:true})).toBeHidden();
      assert.ok(!requests.some(r => r.url === '/fixture-response'));
      await page.getByText('Download and export details', {exact:true}).click();
      await expect(page.getByRole('textbox', {name:'Repository Name'})).toBeVisible();
      await proceed.click();
      await expect.poll(() => requests.filter(r => r.url === '/fixture-response').length).toBe(1);
      assert.equal(JSON.parse(requests.find(r => r.url === '/fixture-response').body).action,'continue');
      assert.ok(!requests.some(r => r.url.includes('/promote?') || r.url.includes('/download?')));
    } finally { await page.close(); }
  });
  for (const delivery of ['empty', 'confirm', 'custom-confirm']) {
    await t.test(`original delivery actions: ${delivery}`, async () => {
      scenario = {status:'planned', validation:'pending', saved:true};
      requests.length = 0;
      const page = await browser.newPage();
      page.on('pageerror', error => console.error(error.stack));
      try {
        await page.goto(`http://127.0.0.1:${server.address().port}/?delivery=${delivery}`);
        await expect(page.getByRole('region', {name:'Artifact review'})).toContainText('Version baseline');
        await expect(page.getByRole('button', {name:'Download Bundle', exact:true})).toHaveCount(0);
        const confirm = page.getByRole('button', {name:'Confirm app bundle', exact:true});
        if (delivery==='empty') await expect(confirm).toHaveCount(0);
        else {
          await expect(page.getByRole('button', {name:delivery==='custom-confirm'?'Return to editor':'Close',exact:true})).toBeVisible();
          await confirm.click();
          await expect.poll(() => requests.filter(r => r.url==='/fixture-response').length).toBe(1);
          const response=JSON.parse(requests.find(r => r.url==='/fixture-response').body);
          assert.equal(response.action,'download_complete');
          assert.equal(response.approved,true);
          assert.equal(response.download_accepted,true);
          assert.ok(!requests.some(r => r.url.includes('/download?')));
          await page.getByRole('textbox', {name:'App change request'}).fill('Change the README.');
          await page.getByRole('button', {name:'Apply change',exact:true}).click();
          await expect(page.getByRole('status', {name:'Refinement result'})).toContainText('Draft saved');
          await expect(confirm).toHaveCount(0);
          assert.equal(requests.filter(r => r.url==='/fixture-response').length,1);
        }
      } finally {await page.close();}
    });
  }
  for (const mode of ['coding_worker', 'surface_regeneration']) for (const item of [
    {status:'planned', validation:'passed', missingProof:true, saved:true, message:'Draft saved; validation is incomplete.', tone:'amber'},
    {status:'planned', validation:'pending', saved:true, message:'Draft saved; validation is incomplete.', tone:'amber'},
    {status:'failed', validation:'failed', saved:true, message:'Draft saved; validation failed.', tone:'red'},
    {status:'validated', validation:'passed', saved:true, message:'Draft validated and saved for review.', tone:'emerald'},
    {status:'failed', validation:'failed', saved:false, message:'Refinement failed; no draft was saved.', tone:'red'},
    {status:'planned', validation:'skipped', saved:false, message:'Refinement planned; no draft was saved.', tone:'amber'},
    {status:'validated', validation:'passed', saved:false, message:'Validation passed, but no saved draft is available.', tone:'amber'},
    {status:'failed', validation:'failed', saved:true, reviewError:true, message:'Draft saved; validation failed.', tone:'red'},
  ]) {
    await t.test(`${mode}: ${item.status}, saved=${item.saved}, reviewError=${Boolean(item.reviewError)}`, async () => {
      scenario = {...item, mode};
      requests.length = 0;
      const page = await browser.newPage();
      page.on('pageerror', error => console.error(error.stack));
      try {
        await page.goto(`http://127.0.0.1:${server.address().port}`);
        await expect(page.getByRole('region', {name:'Artifact review'})).toContainText('Version baseline');
        await expect(page.getByRole('button', {name:'Download Bundle',exact:true})).toBeVisible();
        await page.getByRole('button', {name:'1 warning(s)',exact:true}).click();
        await expect(page.getByText('Parent evidence only', {exact:true})).toBeVisible();
        await page.getByRole('textbox').fill('Change the README.');
        await page.getByRole('button', {name:'Apply change', exact:true}).click();
        const result = page.getByRole('status', {name:'Refinement result'});
        await expect(result).toContainText(item.message);
        await expect(page.getByText('Parent evidence only', {exact:true})).toHaveCount(0);
        await expect(page.getByText('parent-only-strategy', {exact:true})).toHaveCount(0);
        const validationLabel = {passed:'Validation passed', failed:'Validation failed', pending:'Validation pending', skipped:'Validation skipped'}[item.missingProof ? 'pending' : item.validation];
        await expect(page.getByText(validationLabel, {exact:true})).toBeVisible();
        await expect(result).toHaveClass(new RegExp(`border-${item.tone}-`));
        await expect(result).not.toContainText('Scoped refinement applied.');
        await expect(page.getByRole('button', {name:'Download Bundle',exact:true})).toHaveCount(0);
        const advances = item.status === 'validated' && item.saved;
        await expect(page.getByLabel('Preview version')).toHaveText(advances ? 'candidate' : 'baseline');
        await page.getByRole('button', {name:'Split',exact:true}).click();
        await expect(page.getByLabel('Editor contents')).toHaveText(advances ? 'Candidate contents' : 'Original contents');
        if (item.saved && !advances) await expect(result).toContainText('The editor and preview still show version baseline.');
        if (item.status === 'failed') await expect(result).toContainText('Required checks failed.');
        if (item.saved) {
          const panel = page.getByRole('region', {name:'Artifact review'});
          await page.getByRole('button', {name:'Review patch', exact:true}).click();
          await expect(panel).toBeFocused();
          if (item.reviewError) await expect(panel.getByRole('alert')).toHaveText('Saved draft review is unavailable.');
          else {
            await expect(panel).toContainText('Version candidate');
            await expect(panel.getByText('-Original contents\n+Candidate contents', {exact:true})).toBeHidden();
            await panel.getByText('Code changes (1)', {exact:true}).click();
            await expect(panel.getByText('-Original contents\n+Candidate contents', {exact:true})).toBeVisible();
            if (advances) {
              await page.getByRole('button', {name:'Accept artifact', exact:true}).click();
              await expect.poll(() => requests.filter(r => r.method === 'POST' && r.url.includes('/candidate/accept')).length).toBe(1);
            } else {
              await expect(panel).toContainText('Validation has not passed.');
              await expect(page.getByRole('button', {name:'Accept artifact', exact:true})).toHaveCount(0);
              await page.getByRole('button', {name:'Reject artifact', exact:true}).click();
              await expect.poll(() => requests.filter(r => r.method === 'POST' && r.url.includes('/candidate/reject')).length).toBe(1);
            }
          }
        } else {
          await expect(page.getByRole('button', {name:'Review patch', exact:true})).toHaveCount(0);
          assert.ok(!requests.some(r => r.url.includes('/candidate/')));
        }
        assert.equal(requests.filter(r => r.url === '/fixture-trigger').length, 1, 'Review must not execute another refinement');
        assert.ok(!requests.some(r => r.method === 'POST' && r.url.includes('/baseline/')));
        assert.ok(!requests.some(r => r.url === '/fixture-response'), 'Refinement review must not answer the original delivery workflow');
      } finally { await page.close(); }
    });
  }
  for (const validation of ['passed', 'skipped']) {
    await t.test(`AppReview handoff uses actual ${validation} validation for a saved surface draft`, async () => {
      // An inconsistent success label must never override missing build proof.
      scenario = {mode:'surface_regeneration', status:'validated', validation, saved:true};
      requests.length = 0;
      const page = await browser.newPage();
      try {
        await page.goto(`http://127.0.0.1:${server.address().port}/?initial=true`);
        await expect(page.getByRole('status', {name:'Refinement result'})).toContainText(
          validation === 'passed' ? 'Draft validated and saved for review.' : 'Draft saved; validation is incomplete.');
        await expect(page.getByLabel('Preview version')).toHaveText(validation === 'passed' ? 'candidate' : 'baseline');
        await expect(page.getByText('Parent evidence only', {exact:true})).toHaveCount(0);
        await expect(page.getByText(validation === 'passed' ? 'Validation passed' : 'Validation skipped', {exact:true})).toBeVisible();
        await page.getByRole('button', {name:'Split',exact:true}).click();
        await expect(page.getByLabel('Editor contents')).toHaveText(validation === 'passed' ? 'Candidate contents' : 'Original contents');
        await page.getByRole('button', {name:'Review patch',exact:true}).click();
        await expect(page.getByRole('region', {name:'Artifact review'})).toContainText('Version candidate');
        assert.equal(requests.filter(r => r.url === '/fixture-trigger').length, 0);
        assert.ok(!requests.some(r => r.method === 'POST'));
      } finally {await page.close();}
    });
  }
});
