/**
 * Renders every workflow UI component with a representative payload, using the
 * real chat-ui primitives and the real built stylesheet, so they can be looked
 * at. A component that throws is recorded rather than aborting the run.
 *
 * Usage (from web_shell/):  node <this file> <out.html>
 */
import path from 'node:path';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';

const shell = process.cwd();
const wf = path.resolve(shell, '../factory_app/workflows');
const chatUi = path.resolve(shell, '../chat-ui/src');
const OUT = process.argv[2];

const noop = () => {};

// Transition components read their choices from transition.options. Take those
// from the real registry so the harness renders what users actually see.
const registry = JSON.parse(fs.readFileSync(
  path.join(wf, 'extended_orchestration/extension_registry.json'), 'utf8'));
const transitionFor = (id) => {
  const found = (registry.transitions || []).find((t) => t.id === id);
  if (!found) throw new Error(`registry has no transition ${id}`);
  return { ...found, context: {}, transition_type: id };
};

const CASES = [
  ['WorkflowPlanReview', `${wf}/AgentGenerator/ui/WorkflowPlanReview.jsx`, {
    payload: {
      review_id: 'r1', workflow_count: 2, title: 'Proposed workflows',
      summary: 'Two workflows cover the borrow-request lifecycle.',
      workflows: [{ name: 'BorrowRequest' }, { name: 'OverdueReminder' }],
    }, onResponse: noop, workflowName: 'AgentGenerator',
  }],
  ['ActionPlan', `${wf}/AgentGenerator/ui/ActionPlan.js`, {
    payload: {
      actions: [
        { id: 'a1', name: 'create_request', description: 'Member requests a tool' },
        { id: 'a2', name: 'approve_request', description: 'Coordinator approves' },
      ],
    }, onResponse: noop,
  }],
  ['AppReviewSummary', `${wf}/AppReview/ui/AppReview/AppReviewSummary.jsx`, {
    payload: {
      can_promote: true,
      bundle_acceptance: { status: 'passed' },
      build_validation: { status: 'passed' },
      integration_checks: { status: 'attention_required' },
      security_readiness: { status: 'passed' },
    },
  }],
  ['AppIntelligenceOverviewCard', `${wf}/ExistingAppDiscovery/ui/AppIntelligenceOverviewCard.jsx`, {
    payload: {
      app_name: 'neighbourhood-tools', github_repo: 'acme/neighbourhood-tools', status: 'ready',
      app_intelligence_summary: 'Indexed 412 files across 9 modules.',
      catalog: {
        present: true, snapshot_id: 'snap_8812',
        coverage: {
          file_count: 412, symbol_count: 5183, node_count: 1290, edge_count: 3044,
          language_counts: { python: 240, javascript: 130, yaml: 42 },
          role_counts: { handler: 60, service: 48, test: 90 },
        },
        architecture: {
          module_roots: [
            { module_id: 'catalogue', path: 'app/modules/catalogue' },
            { module_id: 'borrowing', path: 'app/modules/borrowing' },
          ],
          service_roots: [{ service_id: 'email', path: 'app/services/integrations/email_client.py' }],
          ui_surfaces: [{ label: 'Catalogue page', path: 'app/ui/pages/catalogue.yaml' }],
          workflow_roots: [],
        },
        capabilities: [{ capability_id: 'borrow.request' }],
        integration_surfaces: [{ label: 'SMTP', kind: 'email' }],
        data_surfaces: [{ label: 'tools', kind: 'collection' }],
        risk_hints: [
          { risk_id: 'untyped_handler', severity: 'medium', description: 'Two handlers lack typed request schemas.' },
          { risk_id: 'missing_policy', severity: 'high', description: 'borrowing module has no policy.py scoping.' },
        ],
        agent_context_policy: {
          policy: 'retrieve_not_dump',
          authority: { summary: 'App Intelligence', relationships: 'Context Graph' },
          surfaces: [{ label: 'module contracts' }],
        },
      },
      warnings: ['3 files skipped: binary'],
    },
  }],
  ['AppIntelligenceProgressCard', `${wf}/ExistingAppDiscovery/ui/AppIntelligenceProgressCard.jsx`, {
    payload: {
      app_intelligence_progress: { stage: 'graph', percent: 62, message: 'Building context graph' },
      github_repo: 'acme/neighbourhood-tools', warnings: ['rate limit backoff applied'],
    },
  }],
  ['RepoAccessRecoveryCard', `${wf}/ExistingAppDiscovery/ui/RepoAccessRecoveryCard.jsx`, {
    payload: {
      github_repo: 'acme/private-repo', http_status: 404,
      message: 'Mozaiks could not read this repository with the available credentials.',
      recovery_actions: [
        { id: '1', kind: 'oauth_retry', label: 'Reconnect GitHub', description: 'Grant read access to private repositories.' },
        { id: '2', kind: 'local_path', label: 'Use a local path', description: 'Point Mozaiks at a checkout on disk.' },
      ],
      app_intelligence_progress: { message: 'Indexing stopped before the tree could be read.' },
    },
  }],
  ['SubscriptionContractReview', `${wf}/SubscriptionContractDesigner/ui/SubscriptionContractDesigner/SubscriptionContractReview.jsx`, {
    payload: {
      review_id: 'sc1',
      plans: [
        { plan_id: 'free', name: 'Free', price: 0, capabilities: ['browse'] },
        { plan_id: 'pro', name: 'Pro', price: 12, capabilities: ['browse', 'borrow', 'reminders'] },
      ],
      capabilities: [{ capability_id: 'borrow' }],
    }, onResponse: noop, workflowName: 'SubscriptionContractDesigner',
  }],
  ['ThemePreviewCard', `${wf}/ThemeCapture/ui/ThemeCapture/components/ThemePreviewCard.js`, {
    payload: {
      theme: { primary: '#3b82f6', secondary: '#10b981', background: '#0b1220', foreground: '#e6edf7' },
      name: 'Soft Civic', description: 'Calm blues and greens with generous whitespace.',
    }, onResponse: noop,
  }],
  ['ConceptBlueprint', `${wf}/ValueEngine/ui/ValueEngine/components/ConceptBlueprint.js`, {
    payload: {
      app_name: 'Neighbourhood Tools',
      tagline: 'Borrow what you need from the people next door.',
      value_proposition: 'A lending library for a neighbourhood association.',
      features: [
        { name: 'Catalogue', description: 'Browse available tools' },
        { name: 'Requests', description: 'Request a borrow window' },
      ],
      personas: [{ name: 'Member' }, { name: 'Coordinator' }],
    }, onResponse: noop,
  }],
  ['UserInputRequest', `${wf}/RuntimeToolCallSmoke/ui/UserInputRequest.js`, {
    payload: { prompt: 'Which database should the generated app use?', options: ['MongoDB', 'Postgres'] },
    onResponse: noop,
  }],
  ['AgentAPIKeysBundleInput', `${wf}/_shared/ui/AgentAPIKeysBundleInput.js`, {
    payload: {
      providers: [
        { id: 'openai', label: 'OpenAI', required: true },
        { id: 'anthropic', label: 'Anthropic', required: false },
      ],
    }, onResponse: noop,
  }],
  ['AppTypeSelector', `${wf}/extended_orchestration/ui/transitions/AppTypeSelector.js`, { transition: transitionFor('app_type_selector'), onResolve: noop }],
  ['BrownfieldPathSelector', `${wf}/extended_orchestration/ui/transitions/BrownfieldPathSelector.js`, { transition: transitionFor('brownfield_path_selector'), onResolve: noop }],
  ['BrownfieldRepoInput', `${wf}/extended_orchestration/ui/transitions/BrownfieldRepoInput.js`, { transition: transitionFor('brownfield_repo_input'), onResolve: noop }],
  ['CodingJourneySelector', `${wf}/extended_orchestration/ui/transitions/CodingJourneySelector.js`, { transition: transitionFor('coding_journey_selector'), onResolve: noop }],
  ['DatabaseSetupSelector', `${wf}/extended_orchestration/ui/transitions/DatabaseSetupSelector.js`, { transition: transitionFor('database_setup_selector'), onResolve: noop }],
];

const require_ = createRequire(import.meta.url);
const results = [];

for (const [name, entry, props] of CASES) {
  let html = '';
  let error = null;
  try {
    const out = await build({
      entryPoints: [entry], bundle: true, write: false, platform: 'node', format: 'cjs',
      jsx: 'automatic', external: ['react', 'react/jsx-runtime', 'react-dom', 'react-dom/server'],
      // Workflow UI ships JSX inside .js files, and imports brand images directly.
      loader: { '.js': 'jsx', '.jpg': 'dataurl', '.png': 'dataurl', '.svg': 'dataurl', '.css': 'empty' },
      // The chat-ui barrel reaches a vite virtual module that only exists in a
      // vite build; stub it so the barrel resolves under esbuild.
      plugins: [{
        name: 'stub-virtual',
        setup(b) {
          b.onResolve({ filter: /^virtual:/ }, (a) => ({ path: a.path, namespace: 'virtual-stub' }));
          b.onLoad({ filter: /.*/, namespace: 'virtual-stub' }, () => ({ contents: 'export default {};', loader: 'js' }));
        },
      }],
      nodePaths: [path.join(shell, 'node_modules')], logLevel: 'silent',
      alias: { '@mozaiks/chat-ui': chatUi },
      define: { 'import.meta.env.DEV': 'false', 'import.meta.env.PROD': 'true' },
    });
    const m = { exports: {} };
    new Function('module', 'exports', 'require', out.outputFiles[0].text)(m, m.exports, require_);
    const Comp = m.exports.default || m.exports[name];
    if (typeof Comp !== 'function') throw new Error(`no component export (got ${typeof Comp})`);
    html = renderToStaticMarkup(createElement(Comp, props));
    if (!html.trim()) throw new Error('rendered empty');
  } catch (e) {
    error = String(e.message || e).split('\n')[0].slice(0, 200);
  }
  results.push({ name, html, error });
  console.log(error ? `FAIL ${name}: ${error}` : `ok   ${name} (${html.length} chars)`);
}

const css = fs.readdirSync('dist/assets').find((f) => f.endsWith('.css'));
// The app sets its --mz-* tokens inline on <html> at runtime, so a static page
// linking only the stylesheet renders colourless. Replay captured real tokens.
const tokenFile = 'dist/theme-tokens.json';
const tokens = fs.existsSync(tokenFile) ? JSON.parse(fs.readFileSync(tokenFile, 'utf8')) : {};
const tokenCss = Object.entries(tokens).map(([k, v]) => `${k}:${v};`).join('');
if (!tokenCss) console.warn('WARNING: no dist/theme-tokens.json — colours will not render');
const page = `<!doctype html><html class="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/assets/${css}">
<style>
:root{${tokenCss}}
body{padding:24px;background:var(--color-background);color:var(--color-foreground);}
.case{margin-bottom:40px}
.case>h2{font:600 13px ui-monospace,monospace;color:var(--color-muted-foreground);margin:0 0 8px;text-transform:uppercase;letter-spacing:.06em}
.err{border:1px solid var(--color-destructive);background:color-mix(in srgb,var(--color-destructive) 12%,transparent);color:var(--color-destructive);padding:12px;border-radius:8px;font:13px ui-monospace,monospace}
</style></head>
<body><div id="harness">
${results.map((r) => `<section class="case" data-case="${r.name}"><h2>${r.name}</h2>${r.error ? `<div class="err">RENDER FAILED: ${r.error}</div>` : r.html}</section>`).join('\n')}
</div></body></html>`;

fs.writeFileSync(OUT, page, 'utf8');
fs.writeFileSync(OUT.replace(/\.html$/, '.json'), JSON.stringify(results.map(({ name, error }) => ({ name, error })), null, 2));
console.log(`\n${results.filter((r) => !r.error).length}/${results.length} rendered -> ${OUT}`);
