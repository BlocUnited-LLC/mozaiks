import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import vm from 'node:vm'

const require = createRequire(new URL('../package.json', import.meta.url))
const { transformSync } = require('esbuild')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const calls = []
let sendReply

const source = readFileSync(new URL('../../factory_app/app/admin/pages/AppSupportPage.jsx', import.meta.url), 'utf8')
const { code } = transformSync(`${source}\nexport { supportRequestToRun, ThreadPanel }`, {
  loader: 'jsx',
  format: 'cjs',
  jsx: 'automatic',
})
const module = { exports: {} }
vm.runInNewContext(code, {
  module,
  exports: module.exports,
  console,
  require: (name) => {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name)
    if (name === 'react-router-dom') return { useParams: () => ({}) }
    if (name === '@mozaiks/chat-ui/workspace') return { WorkspaceLayout: ({ children }) => children }
    if (name === '@mozaiks/chat-ui/ui') {
      return {
        ChatThread: ({ messages, onSend }) => {
          sendReply = onSend
          return React.createElement('div', {
            'data-message-count': messages.length,
            'data-can-send': Boolean(onSend),
          })
        },
      }
    }
    if (name === '../../ui/components/StudioShared.jsx') {
      return {
        Panel: ({ children }) => children,
        StatusPill: ({ children }) => children,
        StudioErrorState: () => null,
        StudioLoadingState: () => null,
      }
    }
    if (name === './AppStudioChrome.jsx') {
      return { WorkspaceStudioHero: () => null, formatCompactNumber: (value) => String(value) }
    }
    if (name === './studioApi.js') {
      return {
        studioFetch: async (...args) => {
          calls.push(args)
          return { ok: true, json: async () => ({ success: true }) }
        },
      }
    }
    throw new Error(`Unexpected import: ${name}`)
  },
})

const { supportRequestToRun, ThreadPanel } = module.exports

const operatorListSource = readFileSync(new URL('../../factory_app/app/admin/pages/UserSupportPage.jsx', import.meta.url), 'utf8')
const { code: operatorListCode } = transformSync(`${operatorListSource}\nexport { normalizeSupportRequest }`, {
  loader: 'jsx',
  format: 'cjs',
  jsx: 'automatic',
})
const operatorListModule = { exports: {} }
vm.runInNewContext(operatorListCode, {
  module: operatorListModule,
  exports: operatorListModule.exports,
  require: (name) => {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name)
    if (name === 'react-router-dom') return { useNavigate: () => () => {} }
    if (name === '@mozaiks/chat-ui/ui') return { CollectionToolbar: () => null, InlineEmptyState: () => null, ResourceList: () => null }
    if (name === '@mozaiks/chat-ui/workspace') return { WorkspaceLayout: ({ children }) => children }
    if (name === '../../ui/components/StudioShared.jsx') {
      return { ActionButton: () => null, StatusPill: () => null, StudioErrorState: () => null, StudioLoadingState: () => null }
    }
    if (name === './AppStudioChrome.jsx') {
      return { WorkspaceStudioHero: () => null, formatCompactNumber: (value) => String(value) }
    }
    if (name === './appStudioModel.js') return { getAppDisplayDescription: () => '', getAppDisplayName: () => '' }
    if (name === './studioApi.js') return { studioFetch: async () => ({ ok: true, json: async () => ({}) }) }
    if (name === './useWorkspaceStudioData.js') return { useWorkspaceStudioData: () => ({}) }
    throw new Error(`Unexpected import: ${name}`)
  },
})

test('operator workspace list labels tickets by subject app', () => {
  const row = operatorListModule.exports.normalizeSupportRequest({
    request_id: 'sr_subject',
    app_id: 'mozaiks-platform',
    app_name: 'Mozaiks Platform',
    subject_app_id: 'customer-a',
  })
  assert.equal(row.appId, 'customer-a')
  assert.equal(row.appName, 'customer-a')
})

test('operator panel shows missing conversation and withholds reply', () => {
  const run = supportRequestToRun({
    request_id: 'sr_missing',
    app_id: 'mozaiks-platform',
    subject_app_id: 'customer-a',
    message: 'Original message',
    status: 'open',
    messages: [],
    error: 'Support conversation unavailable.',
  })
  assert.equal(run.app_id, 'customer-a')
  assert.equal(run.error, 'Support conversation unavailable.')
  assert.equal(run.messages.length, 0)

  const html = renderToStaticMarkup(React.createElement(ThreadPanel, { run }))
  assert.match(html, /role="alert"[^>]*>Support conversation unavailable\./)
  assert.match(html, /data-message-count="0"/)
  assert.match(html, /data-can-send="false"/)
  assert.equal(sendReply, undefined)
})

test('operator reply omits client-selected sender role', async () => {
  calls.length = 0
  const run = supportRequestToRun({ request_id: 'sr_open', message: 'Help', status: 'open' })
  renderToStaticMarkup(React.createElement(ThreadPanel, { run }))
  assert.equal(typeof sendReply, 'function')

  await sendReply('We can help')

  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/api/modules/workspace_support/add_support_message')
  assert.equal(calls[0][1].method, 'POST')
  assert.deepEqual(JSON.parse(calls[0][1].body), { request_id: 'sr_open', message: 'We can help' })
})
