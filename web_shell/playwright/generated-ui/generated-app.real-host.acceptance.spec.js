import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { realHostSettings, routeInventory } from '../fixtures/generatedUiRealHost.js';

const shellRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { profile, runId, baseURL } = realHostSettings(shellRoot);

for (const route of profile.routes.filter((entry) => entry.excluded_reason == null)) {
  test(`real-host route ${route.path}`, async ({ page, request }, testInfo) => {
    const caseId = `route:${route.path}`;
    expect(profile.cases.some((entry) => entry.id === caseId
      && entry.route === route.path && entry.project === testInfo.project.name),
    `Profile must declare ${caseId} for ${testInfo.project.name}`).toBe(true);

    const shellResponse = await request.get('/api/shell-config?surface=platform');
    expect(shellResponse.ok(), 'Real shell configuration must be available').toBe(true);
    const shell = await shellResponse.json();
    expect(shell.appId).toBe(profile.candidate.app_id);
    expect(routeInventory(shell.pages)).toEqual(routeInventory(profile.routes));

    const failures = [];
    page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') failures.push(`console.error: ${message.text()}`);
    });
    const visit = new URL(route.visit_path ?? route.path, baseURL);
    const response = await page.goto(visit.href);
    expect(response?.ok(), `Required page ${route.path} must load successfully`).toBe(true);
    await expect(page).toHaveURL(visit.href);
    const main = page.getByRole('main');
    await expect(main).toBeVisible();
    await expect(main.getByRole('heading', { name: route.heading, exact: true }).first()).toBeVisible();
    expect(failures, 'Required page must not produce browser errors').toEqual([]);

    testInfo.annotations.push(
      { type: 'mozaiks.case_id', description: caseId },
      { type: 'mozaiks.route', description: route.path },
      { type: 'mozaiks.run_id', description: runId },
      { type: 'mozaiks.candidate', description: JSON.stringify(profile.candidate) },
    );
  });
}
