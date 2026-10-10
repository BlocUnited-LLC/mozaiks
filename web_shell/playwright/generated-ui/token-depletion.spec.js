import { expect, test } from '@playwright/test'
import { localDevelopmentAuth } from '../fixtures/localAuth.js'

async function mockShell(page) {
  await page.route('**/api/shell-config', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        version: '1.0.0',
        appName: 'Test App',
        appId: 'test-app',
        auth: localDevelopmentAuth,
        landing_spot: '/token-depletion',
        pages: [
          {
            path: '/token-depletion', component: 'TokenDepletionPage', id: 'token-depletion',
            label: 'Report', order: 10, meta: { title: 'Report', requiresAuth: false },
          },
          {
            path: '/help', component: 'SchemaPage', schema: 'tickets', id: 'help',
            label: 'Help', order: 20, meta: { title: 'Help', requiresAuth: false },
          },
        ],
        header: { logo: { wordmark: 'Test App', href: '/' }, pages: [], actions: [] },
        notifications: { show: false, path: '/notifications' },
        profile: { show: false },
        footer: { visible: false },
        mobile: { bottomBar: { visible: 'auto', items: [] } },
      }),
    })
  )
  for (const pattern of ['**/api/theme-config', '**/api/themes/**']) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    )
  }
  for (const pattern of ['**/api/notifications/count', '**/api/workflows', '**/api/pages/**']) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    )
  }
}

test('terminal token depletion stays on the page with contact guidance and no retry', async ({ page }) => {
  await mockShell(page)
  let actionCalls = 0
  await page.route('**/api/modules/premium_reports/generate_report', (route) => {
    actionCalls += 1
    return route.fulfill({
      status: 402,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: {
          error: 'Insufficient token balance to run this AI action.',
          error_code: 'INSUFFICIENT_TOKENS',
          extra_data: { recovery_action: 'contact_admin' },
        },
      }),
    })
  })

  await page.goto('/token-depletion')
  await page.getByRole('button', { name: 'Generate report' }).click()

  await expect(page.getByRole('alert')).toContainText('Contact an administrator')
  await expect(page.getByRole('button', { name: 'Generate report' })).toBeDisabled()
  await expect(page).toHaveURL(/\/token-depletion$/)
  expect(actionCalls).toBe(1)
})

test('an explicit local contact route opens when one is configured', async ({ page }) => {
  await mockShell(page)
  let actionCalls = 0
  await page.route('**/api/modules/premium_reports/generate_report', (route) => {
    actionCalls += 1
    return route.fulfill({
      status: 402,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: {
          error: 'Insufficient token balance to run this AI action.',
          error_code: 'INSUFFICIENT_TOKENS',
          extra_data: { recovery_action: 'contact_admin', contact_route: '/help' },
        },
      }),
    })
  })

  await page.goto('/token-depletion')
  await page.getByRole('button', { name: 'Generate report' }).click()

  await expect(page).toHaveURL(/\/help$/)
  expect(actionCalls).toBe(1)
})
