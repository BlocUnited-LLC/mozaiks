import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const require = createRequire(new URL('../web_shell/package.json', import.meta.url));
const { transformSync } = require('esbuild');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function load(file, namedExports, imports = {}) {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  const { code } = transformSync(`${source}\nexport { ${namedExports.join(', ')} };`, {
    loader: 'jsx', format: 'cjs', jsx: 'automatic',
  });
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module, exports: module.exports,
    require: (name) => name === 'react/jsx-runtime' ? require(name) : imports[name] || {},
  });
  return module.exports;
}

const overview = load('../factory_app/app/admin/pages/AppOverviewPage.jsx', ['runStatusTone', 'runStatusLabel']);
const portal = load('../factory_app/app/admin/pages/DashboardPortalPage.jsx', ['runTone', 'runLabel']);
for (const [status, label, tone] of [[0, 'Running', 'primary'], [1, 'Completed', 'success'], [2, 'Failed', 'destructive']]) {
  test(`overview status ${status} is ${label}`, () => {
    assert.equal(overview.runStatusLabel(status), label);
    assert.equal(overview.runStatusTone(status), tone);
  });
  test(`dashboard status ${status} is ${label}`, () => {
    assert.equal(portal.runLabel(status), label);
    assert.equal(portal.runTone(status), tone);
  });
}

test('activity renders failed distinctly from completed and in progress', () => {
  const components = {
    useAdminFetch: () => ({ data: { sessions: [0, 1, 2].map((status) => ({ id: String(status), status })) } }),
    Badge: ({ children, variant }) => React.createElement('span', { 'data-variant': variant }, children),
    SectionHeading: ({ children }) => React.createElement('h2', null, children),
  };
  const { SessionsPanel } = load('../chat-ui/src/admin/pages/ActivitySection.jsx', ['SessionsPanel'], {
    '../components/AdminPrimitives.jsx': components,
  });
  const html = renderToStaticMarkup(React.createElement(SessionsPanel));
  assert.match(html, /data-variant="warning">in progress/);
  assert.match(html, /data-variant="success">complete/);
  assert.match(html, /data-variant="error">failed/);
});
