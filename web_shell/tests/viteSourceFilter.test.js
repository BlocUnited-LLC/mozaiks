import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import configureShell from '../vite.config.js';

const shellRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.dirname(shellRoot);

test('the actual shell config compiles JavaScript sources and preserves Vite asset loading', async (t) => {
  const fixturesRoot = path.join(shellRoot, '.local');
  fs.mkdirSync(fixturesRoot, { recursive: true });
  const workspace = fs.mkdtempSync(path.join(fixturesRoot, 'vite-source-filter-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const appRoot = path.join(workspace, 'app');
  const workflowRoot = path.join(workspace, 'workflows');
  const write = (name, content) => {
    const filename = path.join(workspace, name);
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(filename, content);
    return filename;
  };
  write('app/app.json', JSON.stringify({ app_id: 'source-filter-fixture' }));
  fs.mkdirSync(workflowRoot);
  const environment = {
    PLATFORM_PATH: workspace,
    MOZAIKS_WORKFLOWS_PATH: workflowRoot,
    MOZAIKS_FACTORY_APP_PATH: path.join(repoRoot, 'factory_app'),
    MOZAIKS_CHAT_UI_PATH: path.join(repoRoot, 'chat-ui'),
  };
  for (const [key, value] of Object.entries(environment)) {
    const previous = process.env[key];
    process.env[key] = value;
    t.after(() => {
      if (previous === undefined) delete process.env[key];
      else process.env[key] = previous;
    });
  }
  const config = await configureShell({ command: 'build', mode: 'test' });
  const plugin = config.plugins.find((entry) => entry.name === 'jsx-in-js');
  assert.ok(plugin);
  const jsx = 'export default function View() { return <section>jsx-preserved</section>; }';
  const sources = [
    path.join(appRoot, 'ui', 'View.js'),
    path.join(workflowRoot, 'Answer', 'ui', 'View.js'),
    path.join(repoRoot, 'factory_app', 'workflows', 'Answer', 'ui', 'View.js'),
    path.join(repoRoot, 'chat-ui', 'src', 'View.js'),
    path.join(workspace, 'installed', 'mozaiks_chat_ui', 'src', 'View.js'),
  ];

  await t.test('JSX in .js survives source queries and Windows path separators', async () => {
    for (const source of sources) {
      for (const suffix of ['', '?v=123', '?import', '?t=123#fragment', '?draw=1&rawness=1']) {
        const result = await plugin.transform(jsx, source + suffix);
        assert.match(result.code, /jsx-preserved/);
        assert.doesNotMatch(result.code, /<section>/);
      }
      assert.ok(await plugin.transform(jsx, source.replaceAll('/', '\\') + '?v=123'));
    }
  });

  await t.test('non-JavaScript, raw/url, virtual, and unrelated requests bypass JSX transformation', async () => {
    const skipped = [
      ...sources.flatMap((source) => [
        source.replace(/\.js$/, '.css'), source.replace(/\.js$/, '.css?inline'),
        source.replace(/\.js$/, '.json'), source.replace(/\.js$/, '.jsx'),
        source + '?raw', source + '?url', source + '?v=123&raw', source + '?url&v=123',
        source + '?raw=true', source + '?url=true#fragment', '\0' + source,
      ]),
      path.join(appRoot + '-sibling', 'ui', 'View.js'),
      path.join(workspace, 'unrelated', 'View.js'),
    ];
    for (const id of skipped) assert.equal(await plugin.transform('.not JavaScript', id), undefined, id);
  });

  await t.test('production Vite build imports CSS, JSON, raw/url assets and JSX-in-.js together', async () => {
    write('app/ui/View.js', jsx);
    write('app/ui/view.css', '.fixture-style { color: var(--mz-primary); }');
    write('app/ui/payload.json', JSON.stringify({ title: 'json-stays-data' }));
    write('app/ui/literal.js', 'This is raw content, not JavaScript: <raw-asset>');
    const entry = write('app/ui/entry.js', `
      import './view.css';
      import data from './payload.json';
      import raw from './literal.js?raw';
      import url from './literal.js?url';
      import View from './View.js?v=fixture';
      globalThis.mozaiksSourceFixture = { data, raw, url, view: View() };
    `);
    const result = await build({
      ...config,
      configFile: false,
      root: workspace,
      publicDir: false,
      logLevel: 'silent',
      css: { postcss: { plugins: [] } },
      build: {
        write: false,
        minify: false,
        cssMinify: false,
        assetsInlineLimit: 0,
        rolldownOptions: {
          input: entry,
          external: ['react/jsx-runtime'],
          output: { entryFileNames: 'entry.js' },
        },
      },
    });
    const output = result.output;
    const javascript = output.find((item) => item.type === 'chunk' && item.isEntry).code;
    assert.match(javascript, /jsx-preserved/);
    assert.match(javascript, /json-stays-data/);
    assert.match(javascript, /This is raw content, not JavaScript: <raw-asset>/);
    assert.ok(output.some((item) => item.type === 'asset' && item.fileName.endsWith('.css') && String(item.source).includes('.fixture-style')));
    const urlAsset = output.find((item) => item.type === 'asset' && item.fileName.endsWith('.js'));
    assert.equal(String(urlAsset.source), 'This is raw content, not JavaScript: <raw-asset>');
    assert.ok(javascript.includes(urlAsset.fileName));
  });
});
