import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build, createServer, preview } from 'vite';
import configureShell from '../vite.config.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.dirname(shell);
const normalize = (value) => value.replaceAll('\\', '/');
const workbench = normalize(path.join(repo, 'factory_app/workflows/AppGenerator/ui/AppWorkbench.js'));
const sanitizer = normalize(path.join(repo, 'chat-ui/src/utils/monacoDomPurify.js'));
const setup = normalize(path.join(repo, 'factory_app/workflows/AppGenerator/ui/monacoEditor.js'));
const copiedSanitizer = '/monaco-editor/esm/vs/base/browser/dompurify/dompurify.js';

test('AppWorkbench Code and Split use local Monaco with the locked sanitizer in dev and production', { timeout: 240_000 }, async (t) => {
  const temporaryRoot = path.join(shell, '.local');
  fs.mkdirSync(temporaryRoot, { recursive: true });
  const fixture = fs.mkdtempSync(path.join(temporaryRoot, 'monaco-'));
  assert.ok(path.resolve(fixture).startsWith(path.resolve(temporaryRoot) + path.sep));
  t.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const evidence = path.join(repo, '.local/evidence/monaco-sanitizer-source');
  fs.mkdirSync(evidence, { recursive: true });
  const write = (name, content) => {
    const target = path.join(fixture, name);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  };
  write('index.html', '<style>[class="h-[520px]"]{height:520px}</style><div id="root"></div><script type="module" src="/entry.jsx"></script>');
  write('unrelated/dompurify/dompurify.js', 'export default "unrelated sanitizer";');
  write('unrelated/domSanitize.js', 'export { default } from "./dompurify/dompurify.js";');
  write('stubs/workflowStart.js', 'export const useWorkflowStart = () => ({ startWorkflow: async () => {}, starting: false, error: null });');
  write('stubs/workflowSurfaceStyles.js', 'export const workflowSurfaceStyles = { darkPanel: "" }; export const workflowToolbarButtonClass = () => "";');
  write('stubs/workflowPrimitiveUtils.js', 'export const normalizePrimitiveActions = () => [];');
  write('entry.jsx', `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import DOMPurify from 'dompurify';
    import AppWorkbench from ${JSON.stringify(workbench)};
    import unrelated from './unrelated/domSanitize.js';
    window.unrelated = unrelated;
    window.chatPurifier = DOMPurify;
    window.chatHookCalls = 0;
    DOMPurify.addHook('afterSanitizeAttributes', () => window.chatHookCalls++);
    function Fixture() {
      return <AppWorkbench payload={{generated_files: {'example.js': 'const answer = 42;'}}}
        showExportActions={false} />;
    }
    window.getEditor = async () => {
      const { loader } = await import('@monaco-editor/react');
      const monaco = await loader.init();
      return { monaco, editor: monaco.editor.getEditors()[0] };
    };
    createRoot(document.getElementById('root')).render(<Fixture />);
  `);
  const lock = JSON.parse(fs.readFileSync(path.join(shell, 'package-lock.json'), 'utf8'));
  const lockedVersion = lock.packages['node_modules/dompurify'].version;
  assert.equal(lockedVersion, '3.4.16');
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());

  for (const mode of ['dev', 'production']) {
    await t.test(mode, { timeout: 110_000 }, async (t) => {
      const config = await configureShell({ command: mode === 'dev' ? 'serve' : 'build', mode: 'test' });
      config.resolve.alias = {
        '@mozaiks/chat-ui/hooks/useWorkflowStart.js': path.join(fixture, 'stubs/workflowStart.js'),
        '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js': path.join(fixture, 'stubs/workflowSurfaceStyles.js'),
        '@mozaiks/chat-ui/core/ui/workflowPrimitiveUtils.js': path.join(fixture, 'stubs/workflowPrimitiveUtils.js'),
        ...config.resolve.alias,
      };
      const moduleIds = new Set();
      config.plugins.unshift({
        name: 'fixture-workbench-boundaries',
        enforce: 'pre',
        resolveId(source) {
          if (source === '../../_shared/ui/app_preview/useSandbox') return '\0fixture-sandbox';
          if (source === '../../../app/admin/pages/studioApi.js') return '\0fixture-studio-api';
          if (source === '../../_shared/ui/app_preview/refinementOutput') return '\0fixture-refinement-output';
          if (source === '../../_shared/ui/app_preview/PreviewPane') return '\0fixture-preview';
          if (source === './BuildStatusPane') return '\0fixture-build-status';
          if (source === './ExportActions') return '\0fixture-export-actions';
          if (source === '../../../app/ui/components/HarnessDecisionCard.jsx') return '\0fixture-decision-card';
        },
        load(id) {
          if (id === '\0fixture-sandbox') return 'export const useSandbox = () => ({ syncAndRestart() {}, stopPreview() {} });';
          if (id === '\0fixture-studio-api') return 'export const studioFetch = () => { throw new Error("Unexpected Studio request"); };';
          if (id === '\0fixture-refinement-output') return 'export const refinementOutput = () => null;';
          if (['\0fixture-preview', '\0fixture-build-status', '\0fixture-export-actions', '\0fixture-decision-card'].includes(id)) return 'export default function FixtureBoundary() { return null; }';
        },
      });
      config.plugins.push({
        name: 'observe-real-monaco-sanitizer',
        transform(code, id) {
          const source = normalize(id.split('?', 1)[0]);
          moduleIds.add(source);
          // Observe the actual imported instance without adding a product debug API.
          if (source === sanitizer) return code + `
            window.monacoPurifier = purifier;
            window.monacoSanitizeCalls = 0;
            const sanitize = purifier.sanitize;
            purifier.sanitize = (...args) => { window.monacoSanitizeCalls++; return sanitize(...args); };
          `;
          if (source === setup) return code + `
            window.workerLabels = [];
            window.workerErrors = [];
            const getWorker = self.MonacoEnvironment.getWorker;
            self.MonacoEnvironment.getWorker = (...args) => {
              window.workerLabels.push(args[1]);
              const worker = getWorker(...args);
              worker.addEventListener('error', (error) => window.workerErrors.push(error.message));
              return worker;
            };
          `;
        },
        generateBundle(_options, bundle) {
          for (const id of this.getModuleIds()) moduleIds.add(normalize(id));
          fs.writeFileSync(path.join(evidence, `${mode}-module-graph.json`), JSON.stringify(Object.values(bundle).filter((entry) => entry.type === 'chunk').map((entry) => ({ file: entry.fileName, imports: entry.imports, dynamicImports: entry.dynamicImports, modules: Object.keys(entry.modules) })), null, 2));
        },
      });
      const options = {
        ...config, configFile: false, root: fixture, publicDir: false, logLevel: 'error',
        optimizeDeps: {
          ...config.optimizeDeps,
          noDiscovery: true,
          include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'dompurify'],
        },
        css: { postcss: { plugins: [] } },
        cacheDir: path.join(fixture, 'cache'),
        server: { host: '127.0.0.1', port: 0, strictPort: false, fs: { allow: [repo] } },
        preview: { host: '127.0.0.1', port: 0, strictPort: false },
        build: { ...config.build, outDir: path.join(fixture, 'dist'), emptyOutDir: true },
      };
      let server;
      if (mode === 'dev') {
        server = await createServer(options);
        await server.listen();
      } else {
        await build(options);
        server = await preview(options);
      }
      t.after(() => server.close());
      const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
      const page = await browser.newPage();
      t.after(async () => {
        await page.screenshot({ path: path.join(evidence, `${mode}-final.png`), fullPage: true });
        await page.close();
      });
      const external = [];
      const errors = [];
      const requests = [];
      const workerUrls = [];
      page.on('pageerror', (error) => errors.push(error.message));
      page.on('request', (request) => requests.push(request.url()));
      page.on('worker', (worker) => workerUrls.push(worker.url()));
      await page.route('**/*', (route) => {
        const url = route.request().url();
        if (new URL(url).origin === origin) return route.continue();
        external.push(url);
        return route.abort();
      });
      await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      try {
        await expect(page.getByRole('button', { name: 'Code' })).toBeVisible({ timeout: 30_000 });
      } catch (error) {
        assert.deepEqual(errors, [], 'AppWorkbench failed to mount without a browser error');
        throw error;
      }
      await expect(page.getByRole('button', { name: 'Split' })).toBeVisible();
      assert.equal(await page.evaluate(() => Boolean(window.monacoPurifier)), false, 'editor remains lazy before code is opened');
      assert.equal(requests.some((url) => url.includes('/monaco-editor/')), false);
      const firstView = mode === 'dev' ? 'Code' : 'Split';
      const secondView = mode === 'dev' ? 'Split' : 'Code';
      await page.getByRole('button', { name: firstView }).click();
      await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 30_000 });
      await page.getByRole('button', { name: secondView }).click();
      await expect(page.locator('.monaco-editor').first()).toBeVisible();
      assert.equal(await page.evaluate(() => window.monacoPurifier.version), lockedVersion);
      assert.equal(await page.evaluate(() => window.monacoPurifier === window.chatPurifier), false);
      assert.equal(await page.evaluate(() => window.unrelated), 'unrelated sanitizer');
      await page.locator('.monaco-editor .view-lines').click();
      await page.keyboard.press('ControlOrMeta+A');
      await page.keyboard.insertText('const edited = 7;');
      await expect.poll(() => page.evaluate(async () => (await window.getEditor()).editor.getValue())).toBe('const edited = 7;');

      await page.evaluate(async () => {
        const { monaco, editor } = await window.getEditor();
        monaco.languages.registerHoverProvider('javascript', {
          provideHover: () => ({ contents: [{ value: '<strong>Safe hover</strong><a href="javascript:alert(1)">Unsafe link</a>', supportHtml: true }] }),
        });
        editor.setPosition({ lineNumber: 1, column: 7 });
        editor.trigger('test', 'editor.action.showHover', {});
      });
      await expect(page.locator('.monaco-hover:visible')).toContainText('Safe hover');
      await page.screenshot({ path: path.join(evidence, `${mode}-editor.png`), fullPage: true });
      assert.ok(await page.evaluate(() => window.monacoSanitizeCalls > 0));
      assert.equal(await page.locator('.monaco-hover [href^="javascript:"]').count(), 0);
      assert.equal(await page.evaluate(() => {
        const before = window.chatHookCalls;
        window.chatPurifier.sanitize('<b>Chat still uses its own hook</b>');
        return window.chatHookCalls > before;
      }), true, 'Monaco hook cleanup preserves the chat sanitizer hooks');

      const diagnostics = await page.evaluate(async () => {
        const { monaco } = await window.getEditor();
        const model = monaco.editor.createModel('const value: number = "wrong";', 'typescript', monaco.Uri.parse('file:///worker.ts'));
        const worker = await (await monaco.typescript.getTypeScriptWorker())(model.uri);
        const result = await worker.getSemanticDiagnostics(model.uri.toString());
        model.dispose();
        return result.map((entry) => entry.code);
      });
      assert.ok(diagnostics.includes(2322), 'TypeScript worker returns real diagnostics');
      for (const [language, content] of [['json', '{"value":1}'], ['css', 'body{color:red}'], ['html', '<html><head><title>Example</title></head><body><div>text</div></body></html>']]) {
        await page.evaluate(async ({ language, content }) => {
          const { monaco, editor } = await window.getEditor();
          editor.setModel(monaco.editor.createModel(content, language, monaco.Uri.parse('file:///format.' + language)));
        }, { language, content });
        await expect.poll(async () => page.evaluate(async () => {
          const { editor } = await window.getEditor();
          await editor.getAction('editor.action.formatDocument').run();
          return editor.getValue();
        }), { timeout: 15_000 }).not.toBe(content);
      }
      await page.evaluate(async () => {
        const { monaco } = await window.getEditor();
        const container = document.createElement('div');
        container.style.height = '200px';
        document.body.appendChild(container);
        window.diffEditor = monaco.editor.createDiffEditor(container);
        window.diffEditor.setModel({
          original: monaco.editor.createModel('before', 'plaintext'),
          modified: monaco.editor.createModel('after', 'plaintext'),
        });
      });
      await expect.poll(() => page.evaluate(() => window.diffEditor.getLineChanges()?.length)).toBe(1);
      await expect.poll(() => page.evaluate(() => window.workerLabels)).toEqual(expect.arrayContaining(['editorWorkerService', 'typescript', 'json', 'css', 'html']));
      assert.ok(workerUrls.length >= 5);
      assert.ok(workerUrls.every((url) => new URL(url).origin === origin), 'workers are local assets');
      assert.ok(moduleIds.has(sanitizer), 'actual shell resolver loads the patched adapter');
      assert.equal([...moduleIds].some((id) => id.endsWith(copiedSanitizer)), false, 'copied sanitizer is absent from the module graph');
      assert.deepEqual(external, [], 'no external or CDN fallback request');
      assert.deepEqual(errors, []);
      assert.deepEqual(await page.evaluate(() => window.workerErrors), []);
      const receipt = await page.evaluate(() => ({
        sanitizerVersion: window.monacoPurifier.version,
        sanitizerCalls: window.monacoSanitizeCalls,
        separateInstances: window.monacoPurifier !== window.chatPurifier,
        workerLabels: window.workerLabels,
      }));
      fs.writeFileSync(path.join(evidence, `${mode}-receipt.json`), JSON.stringify({ ...receipt, externalRequests: external, workerUrls, copiedSanitizerInGraph: false }, null, 2));
    });
  }
});
