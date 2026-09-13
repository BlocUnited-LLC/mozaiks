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

const source = readFileSync(new URL('../../factory_app/app/admin/pages/UserSupportPanel.jsx', import.meta.url), 'utf8')
const { code } = transformSync(`${source}\nexport { normaliseRequest, postMessage }`, {
  loader: 'jsx',
  format: 'cjs',
  jsx: 'automatic',
})
const module = { exports: {} }
vm.runInNewContext(code, {
  module,
  exports: module.exports,
  URLSearchParams,
  console: { info() {}, warn() {} },
  require: (name) => {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name)
    if (name === 'react-router-dom') return { useLocation: () => ({ search: '' }) }
    if (name === '@mozaiks/chat-ui/ui') {
      return {
        ChatThread: ({ messages, onSend }) => React.createElement('div', {
          'data-message-count': messages.length,
          'data-can-send': Boolean(onSend),
        }),
      }
    }
    if (name === './studioApi.js') {
      return {
        studioModuleAction: async (...args) => {
          calls.push(args)
          return { success: true }
        },
      }
    }
    throw new Error(`Unexpected import: ${name}`)
  },
})

const { default: UserSupportPanel, normaliseRequest, postMessage } = module.exports

test('profile tickets group and label by subject app rather than runtime host', () => {
  const appA = normaliseRequest({ request_id: 'sr_a', app_id: 'mozaiks-platform', subject_app_id: 'customer-a' })
  const appB = normaliseRequest({ request_id: 'sr_b', app_id: 'mozaiks-platform', subject_app_id: 'customer-b' })
  assert.equal(appA.appId, 'customer-a')
  assert.equal(appA.appLabel, 'customer-a')
  assert.equal(appB.appId, 'customer-b')
  assert.equal(appB.appLabel, 'customer-b')

  const html = renderToStaticMarkup(React.createElement(UserSupportPanel, {
    page: { id: 'support-tickets' },
    data: { requests: [
      { request_id: 'sr_a', app_id: 'mozaiks-platform', subject_app_id: 'customer-a', status: 'open' },
      { request_id: 'sr_b', app_id: 'mozaiks-platform', subject_app_id: 'customer-b', status: 'open' },
    ] },
  }))
  assert.match(html, />customer-a</)
  assert.match(html, />customer-b</)
})

test('missing linked conversation is visible and cannot accept a reply', () => {
  const request = normaliseRequest({
    request_id: 'sr_missing',
    app_id: 'mozaiks-platform',
    subject_app_id: 'customer-a',
    message: 'Original message',
    status: 'open',
    messages: [],
    error: 'Support conversation unavailable.',
  })
  assert.equal(request.error, 'Support conversation unavailable.')
  assert.equal(request.messages.length, 0)

  const html = renderToStaticMarkup(React.createElement(UserSupportPanel, {
    page: { id: 'support-tickets' },
    data: { requests: [{
      request_id: 'sr_missing',
      app_id: 'mozaiks-platform',
      subject_app_id: 'customer-a',
      message: 'Original message',
      status: 'open',
      messages: [],
      error: 'Support conversation unavailable.',
    }] },
  }))
  assert.match(html, /role="alert"[^>]*>Support conversation unavailable\./)
  assert.match(html, /data-message-count="0"/)
  assert.match(html, /data-can-send="false"/)
})

test('profile reply leaves sender role to the authenticated module service', async () => {
  calls.length = 0
  await postMessage({ requestId: 'sr_1', message: 'Please help' })
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], 'workspace_support')
  assert.equal(calls[0][1], 'add_support_message')
  assert.deepEqual({ ...calls[0][2] }, { request_id: 'sr_1', message: 'Please help' })
})
