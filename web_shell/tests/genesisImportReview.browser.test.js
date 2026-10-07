import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import http from 'node:http'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { chromium, expect } from '@playwright/test'
import { build } from 'esbuild'

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const root = path.dirname(shell)
const archive = Buffer.from('neutral imported source archive')
const archiveSha256 = createHash('sha256').update(archive).digest('hex')
const manifestSha256 = 'b'.repeat(64)
const artifactId = 'av_imported'
const targetAppId = 'existing-app'
const buildRegistryId = 'factory-1'
const claim = {
  status: 'reserved', build_record_id: artifactId, bundle_name: 'existing-app-source',
  bundle_sha256: archiveSha256, manifest_sha256: manifestSha256,
  revision_id: 'a'.repeat(40), tree_id: 'c'.repeat(40), source_id: 'owner/repo',
}
const version = {
  id: artifactId, app_id: targetAppId, version_number: 1,
  lifecycle_status: 'draft', validation_status: 'pending', app_validation_status: 'pending',
  commit_metadata: { metadata: { bundle_mode: 'brownfield_genesis_import' } },
}

async function fixture(t) {
  const stubs = {
    '@mozaiks/chat-ui': 'export const UIToolRenderer = () => <p>Generic AppWorkbench</p>;',
    '@mozaiks/chat-ui/workspace': 'export const WorkspaceLayout = ({children}) => <div>{children}</div>;',
    'StudioShared.jsx': `
      export const ActionButton = ({children, onClick, disabled}) => <button onClick={onClick} disabled={disabled}>{children}</button>;
      export const Panel = ({title, children}) => <section><h2>{title}</h2>{children}</section>;
      export const StatusPill = ({children}) => <span>{children}</span>;
      export const StudioErrorState = ({title, message}) => <div role="alert">{title}: {message}</div>;
      export const StudioInlineEmptyState = ({title}) => <p>{title}</p>;
      export const StudioLoadingState = ({label}) => <p>{label}</p>;
    `,
    'CarryForwardReportSummary.jsx': 'export default function Report() { return null; }',
    'AppStudioChrome.jsx': `
      export default function Hero({title}) { return <h1>{title}</h1>; }
      export const formatDateTimeLabel = () => 'Today';
    `,
    'appStudioDataHelpers.js': `
      export const getAppStudioSnapshot = (appId, data) => ({ buildHistory: data.buildHistory.artifact_versions });
    `,
    'useAppStudioData.js': `
      export const useAppStudioData = () => ({ data: window.fixtureData, loading: false, error: null, dataMode: 'live', refresh() {} });
    `,
  }
  const compiled = await build({
    stdin: {
      resolveDir: shell, loader: 'jsx', contents: `
        import React from 'react';
        import { createRoot } from 'react-dom/client';
        import { BrowserRouter, Route, Routes } from 'react-router-dom';
        import AppBuildReviewPage from ${JSON.stringify(path.join(root, 'factory_app/app/admin/pages/AppBuildReviewPage.jsx'))};
        window.mozaiksAuth = { getAccessToken: () => 'fixture-owner-token' };
        createRoot(document.getElementById('root')).render(
          <BrowserRouter><Routes><Route path="/apps/:appId/activity" element={<AppBuildReviewPage />} /></Routes></BrowserRouter>
        );
      `,
    },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    nodePaths: [path.join(shell, 'node_modules')],
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
      'react-router-dom': path.join(shell, 'node_modules/react-router-dom'),
    },
    plugins: [{ name: 'isolated-studio', setup(builder) {
      builder.onResolve({ filter: /@mozaiks\/chat-ui|(?:StudioShared|CarryForwardReportSummary|AppStudioChrome|appStudioDataHelpers|useAppStudioData)\.(?:jsx|js)$/ }, args => ({ path: args.path, namespace: 'fixture' }))
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({
        loader: 'jsx', resolveDir: shell,
        contents: stubs[args.path] || stubs[path.basename(args.path)],
      }))
    } }],
  })
  const html = '<!doctype html><div id="root"></div><script type="module" src="/fixture.js"></script>'
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html')
    response.end(request.url === '/fixture.js' ? compiled.outputFiles[0].text : html)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const browser = await chromium.launch({ headless: true })
  t.after(() => browser.close())
  const baseUrl = `http://127.0.0.1:${server.address().port}`

  return async (subtest, {
    imported = true, selectedAppId = targetAppId, reviewAppId = targetAppId,
    reviewFailure = false, mismatchedClaim = false, badArchive = false, acceptanceFailure = false,
  } = {}) => {
    const page = await browser.newPage({ acceptDownloads: true })
    subtest.after(() => page.close())
    const selected = imported ? { ...version, app_id: selectedAppId } : {
      ...version, id: 'av_generated', commit_metadata: { metadata: {} },
    }
    const calls = { review: 0, download: 0, acceptance: [], bundle: 0, unexpected: [], errors: [] }
    await page.addInitScript(data => { window.fixtureData = data }, {
      summary: { app: {} }, buildRegistryId,
      buildHistory: { artifact_versions: [selected] },
    })
    page.on('pageerror', error => calls.errors.push(error.message))
    subtest.after(() => {
      assert.deepEqual(calls.errors, [])
      assert.deepEqual(calls.unexpected, [])
    })
    await page.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin === baseUrl && (request.isNavigationRequest() || url.pathname === '/fixture.js')) return route.continue()
      if (url.origin !== baseUrl) { calls.unexpected.push(request.url()); return route.abort() }
      if (url.pathname.endsWith('/review')) {
        calls.review += 1
        assert.equal(url.searchParams.get('build_registry_id'), buildRegistryId)
        if (reviewFailure && calls.review === 1) return route.fulfill({ status: 503, json: { detail: 'Review temporarily unavailable' } })
        return route.fulfill({ json: {
          app_id: reviewAppId, artifact_version: { ...version, app_id: reviewAppId },
          genesis_import: mismatchedClaim ? { ...claim, build_record_id: 'av_another' } : claim,
        } })
      }
      if (url.pathname.endsWith('/download')) {
        calls.download += 1
        assert.equal(request.headers().authorization, 'Bearer fixture-owner-token')
        return route.fulfill({ body: badArchive && calls.download === 1 ? Buffer.from('altered archive') : archive, contentType: 'application/zip' })
      }
      if (url.pathname.endsWith('/accept-genesis')) {
        calls.acceptance.push({ body: request.postDataJSON(), headers: request.headers() })
        if (acceptanceFailure && calls.acceptance.length === 1) {
          return route.fulfill({ status: 409, json: { detail: 'Source reservation changed' } })
        }
        return route.fulfill({ json: {
          accepted: true, app_id: targetAppId,
          genesis_import: { ...claim, status: 'accepted', acceptance: { accepted_by: 'owner' } },
          artifact_version: { ...version, lifecycle_status: 'current', validation_status: 'passed', app_validation_status: 'passed' },
        } })
      }
      if (url.pathname.endsWith('/bundle')) {
        calls.bundle += 1
        return route.fulfill({ json: {
          app_id: targetAppId, artifact_version_id: 'av_generated', build_family: 'app_bundle',
          build_key: 'app_bundle', workbench_ui: { component: 'AppWorkbench', workflow_name: 'AppGenerator' },
          workbench: { artifact_version_id: 'av_generated', target_app_id: targetAppId, build_registry_id: buildRegistryId, build_family: 'app_bundle' },
        } })
      }
      calls.unexpected.push(request.url())
      return route.abort()
    })
    await page.goto(`${baseUrl}/apps/${targetAppId}/activity`)
    return { page, calls }
  }
}

test('imported Genesis requires verified archive inspection and exact digest acknowledgement', async t => {
  const open = await fixture(t)
  await t.test('acceptance is scoped to the selected claim and never opens generic review', async subtest => {
    const { page, calls } = await open(subtest)
    await expect(page.getByRole('heading', { name: 'Review imported Genesis source' })).toBeVisible()
    await expect(page.getByText(archiveSha256)).toBeVisible()
    await expect(page.getByText(manifestSha256)).toBeVisible()
    await expect(page.getByText('Generic AppWorkbench')).toHaveCount(0)
    const accept = page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })
    const acknowledgement = page.getByRole('checkbox')
    await expect(accept).toBeDisabled()
    await expect(acknowledgement).toBeDisabled()
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download verified source archive' }).click()
    assert.equal((await download).suggestedFilename(), 'existing-app-source.zip')
    await expect(acknowledgement).toBeEnabled()
    await expect(accept).toBeDisabled()
    await acknowledgement.check()
    await accept.click()
    await expect(page.getByText('Accepted baseline')).toBeVisible()
    await expect(page.getByText('It has not been deployed.', { exact: false })).toBeVisible()
    assert.equal(calls.bundle, 0)
    assert.equal(calls.acceptance.length, 1)
    assert.equal(calls.acceptance[0].headers.authorization, 'Bearer fixture-owner-token')
    assert.deepEqual(calls.acceptance[0].body, {
      confirm_exact_source_review: true,
      reviewed_bundle_sha256: archiveSha256,
      reviewed_manifest_sha256: manifestSha256,
    })
  })
  await t.test('changed download bytes fail closed and can be retried', async subtest => {
    const { page, calls } = await open(subtest, { badArchive: true })
    await expect(page.getByRole('heading', { name: 'Review imported Genesis source' })).toBeVisible()
    await page.getByRole('button', { name: 'Download verified source archive' }).click()
    await expect(page.getByRole('alert')).toContainText('SHA-256 differs')
    await expect(page.getByRole('checkbox')).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })).toBeDisabled()
    assert.equal(calls.acceptance.length, 0)
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download verified source archive' }).click()
    await download
    await expect(page.getByRole('checkbox')).toBeEnabled()
  })
  await t.test('failed review load offers a retry without exposing acceptance', async subtest => {
    const { page, calls } = await open(subtest, { reviewFailure: true })
    await expect(page.getByRole('alert')).toContainText('Review temporarily unavailable')
    await expect(page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Retry loading review' }).click()
    await expect(page.getByRole('heading', { name: 'Review imported Genesis source' })).toBeVisible()
    assert.equal(calls.review, 2)
  })
  await t.test('a claim for another artifact fails closed before download or acceptance', async subtest => {
    const { page, calls } = await open(subtest, { mismatchedClaim: true })
    await expect(page.getByRole('alert')).toContainText('does not match the selected imported source')
    await expect(page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })).toHaveCount(0)
    assert.equal(calls.download, 0)
    assert.equal(calls.acceptance.length, 0)
  })
  await t.test('a selected source for another route app never loads or accepts', async subtest => {
    const { page, calls } = await open(subtest, { selectedAppId: 'another-app' })
    await expect(page.getByRole('alert')).toContainText('selected Genesis source belongs to another app')
    await expect(page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })).toHaveCount(0)
    assert.equal(calls.review, 0)
    assert.equal(calls.acceptance.length, 0)
  })
  await t.test('a review target for another route app cannot enable source acceptance', async subtest => {
    const { page, calls } = await open(subtest, { reviewAppId: 'another-app' })
    await expect(page.getByRole('alert')).toContainText('does not match the selected imported source')
    await expect(page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })).toHaveCount(0)
    assert.equal(calls.review, 1)
    assert.equal(calls.acceptance.length, 0)
  })
  await t.test('409 reloads source review and requires a new download and acknowledgement', async subtest => {
    const { page, calls } = await open(subtest, { acceptanceFailure: true })
    await expect(page.getByRole('heading', { name: 'Review imported Genesis source' })).toBeVisible()
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download verified source archive' }).click()
    await download
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Accept exact source as Genesis baseline' }).click()
    await expect.poll(() => calls.review).toBe(2)
    await expect(page.getByRole('alert')).toContainText('Source reservation changed')
    await expect(page.getByRole('checkbox')).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Accept exact source as Genesis baseline' })).toBeDisabled()
    assert.equal(calls.acceptance.length, 1)
    const secondDownload = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download verified source archive' }).click()
    await secondDownload
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Accept exact source as Genesis baseline' }).click()
    await expect(page.getByText('Accepted baseline')).toBeVisible()
    assert.equal(calls.download, 2)
    assert.equal(calls.acceptance.length, 2)
    assert.deepEqual(calls.acceptance[0].body, calls.acceptance[1].body)
  })
  await t.test('ordinary generated versions retain their saved workbench', async subtest => {
    const { page, calls } = await open(subtest, { imported: false })
    await expect(page.getByText('Generic AppWorkbench')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Review imported Genesis source' })).toHaveCount(0)
    assert.equal(calls.bundle, 1)
    assert.equal(calls.review, 0)
  })
})
