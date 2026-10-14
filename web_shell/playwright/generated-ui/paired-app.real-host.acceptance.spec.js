import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { loopbackBaseUrl, realHostSettings, routeInventory } from '../fixtures/generatedUiRealHost.js';

const shellRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { profile, runId, baseURL } = realHostSettings(shellRoot);
if (!process.env.COMMUNITY_LIVE_CONFIG) throw new Error('Paired acceptance requires COMMUNITY_LIVE_CONFIG.');
const environment = JSON.parse(readFileSync(process.env.COMMUNITY_LIVE_CONFIG, 'utf8'));
if (loopbackBaseUrl(environment.base_url) !== baseURL) {
  throw new Error('The synthetic identity environment must belong to the reviewed browser host.');
}
const apiURL = loopbackBaseUrl(environment.api_url);
const issuer = new URL(environment.auth_issuer);
loopbackBaseUrl(issuer.origin);
const requiredRoutes = profile.routes.filter((entry) => entry.excluded_reason == null);
const paths = requiredRoutes.map((route) => route.path).sort();
if (!['/board,/projects', '/projects,/tasks'].includes(paths.join(','))) {
  throw new Error('Paired acceptance requires /projects and exactly one of /tasks or /board.');
}

// Every case starts a real browser login. API setup uses only the resulting
// authenticated session, against the disposable database owned by the driver.
async function signIn(page, username, route) {
  const user = environment.users.find((entry) => entry.username === username);
  if (!user) throw new Error(`Missing synthetic ${username} identity.`);
  await page.goto(new URL(route, baseURL).href);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL((url) => url.origin === issuer.origin
    && url.pathname === `${issuer.pathname}/protocol/openid-connect/auth`);
  await page.getByLabel('Username or email', { exact: true }).fill(username);
  await page.getByLabel('Password', { exact: true }).fill(user.password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await expect(page).toHaveURL(new URL(route, baseURL).href);
  await expect.poll(() => page.evaluate(async () => Boolean(
    (await window.mozaiksAuth?.getCurrentUser())?.id,
  ))).toBe(true);
}

async function moduleAction(page, moduleId, actionId, params = {}) {
  const token = await page.evaluate(() => window.mozaiksAuth.getAccessToken());
  expect(typeof token, 'The real sign-in must produce an access token').toBe('string');
  const response = await page.request.post(`${apiURL}/api/modules/${moduleId}/${actionId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { params },
  });
  expect(response.status(), `${moduleId}/${actionId} must succeed on the real host`).toBe(200);
  return response.json();
}

function mutationResponse(page, moduleId, actionId) {
  return page.waitForResponse((response) => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/modules/${moduleId}/${actionId}`, { timeout: 30_000 });
}

async function mutationItem(pending, identityField) {
  const response = await pending;
  expect(response.status(), 'UI mutation must succeed on the real host').toBe(200);
  const body = await response.json();
  expect(body.item?.[identityField]).toEqual(expect.any(String));
  return body.item;
}

async function selectRecord(page, name) {
  // DataTable renders desktop rows and mobile cards together; select only the
  // visible presentation, using the actual fixture's single-selection control.
  const record = page.getByRole('main').locator('tr:visible, article:visible')
    .filter({ has: page.getByText(name, { exact: true }) });
  await expect(record).toHaveCount(1);
  await record.getByRole('radio').check();
}

async function screenshot(page, testInfo, name) {
  await testInfo.attach(name, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
}

async function noHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    'The generated page must fit the viewport').toBe(true);
}

async function exerciseDeletion(page, testInfo, entity, label, recordId) {
  await page.reload();
  const main = page.getByRole('main');
  await main.getByRole('searchbox').fill(label);
  await selectRecord(page, label);
  await main.getByRole('button', { name: `Delete ${entity}`, exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: `Delete ${entity}`, exact: true });
  await expect(confirmation.getByRole('textbox')).toHaveValue(label);
  await expect(confirmation.getByRole('textbox')).toBeDisabled();
  const pending = mutationResponse(page, `${entity}s`, `delete_${entity}`);
  await confirmation.getByRole('button', { name: `Delete ${entity}`, exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  expect((await response.json()).deleted).toBe(true);
  await expect(confirmation).toBeHidden();
  await page.reload();
  await main.getByRole('searchbox').fill(label);
  await expect(main.getByText(label, { exact: true })).toHaveCount(0);
  const remaining = await moduleAction(page, `${entity}s`, `list_${entity}s`, { search: recordId });
  expect(remaining.total).toBe(0);
  expect(remaining.items).toEqual([]);
  await screenshot(page, testInfo, `${entity}-deleted-after-reload`);
}

async function exerciseProfile(page, testInfo, label) {
  const userId = await page.evaluate(async () => (await window.mozaiksAuth.getCurrentUser()).id);
  const responseFor = (method) => page.waitForResponse((response) => response.url() === `${apiURL}/api/me`
    && response.request().method() === method);
  const readProfile = async (pending) => {
    const response = await pending;
    expect(response.status(), 'Inherited profile must use the authenticated host').toBe(200);
    const body = await response.json();
    expect(body.app_id).toBe(profile.candidate.app_id);
    expect(body.user_id).toBe(userId);
    return body;
  };
  const loaded = responseFor('GET');
  await page.goto(new URL('/me', baseURL).href);
  const before = await readProfile(loaded);
  await expect(page.getByRole('heading', { name: before.display_name || before.username, exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Edit profile', exact: true }).click();
  const displayName = `${label} member`;
  const bio = 'Profile saved through the inherited authenticated page and verified after reload.';
  await page.getByPlaceholder('Display name', { exact: true }).fill(displayName);
  await page.getByPlaceholder('Tell people a bit about yourself', { exact: true }).fill(bio);
  const saved = responseFor('PUT');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  const updated = await readProfile(saved);
  expect(updated.display_name).toBe(displayName);
  expect(updated.bio).toBe(bio);
  await expect(page.getByRole('heading', { name: displayName, exact: true })).toBeVisible();
  const reloaded = responseFor('GET');
  await page.reload();
  const persisted = await readProfile(reloaded);
  expect(persisted.display_name).toBe(displayName);
  expect(persisted.bio).toBe(bio);
  await expect(page.getByRole('heading', { name: displayName, exact: true })).toBeVisible();
  await expect(page.getByText(bio, { exact: true })).toBeVisible();
  await screenshot(page, testInfo, 'inherited-profile-persisted');
  await page.goto(new URL('/projects', baseURL).href);
  await expect(page.getByRole('main').getByRole('heading', { name: 'Projects', exact: true })).toBeVisible();
  await page.getByRole('main').getByRole('searchbox').fill(label);
  await selectRecord(page, label);
}

async function exerciseAssistant(page, testInfo) {
  const lifecycle = { sent: [], received: [], sessions: [], workflowStarts: 0 };
  page.on('websocket', (socket) => {
    for (const [event, field] of [['framesent', 'sent'], ['framereceived', 'received']]) {
      socket.on(event, ({ payload }) => {
        let message;
        try { message = JSON.parse(payload.toString()); } catch { return; }
        if (typeof message.type === 'string') lifecycle[field].push(message.type);
        if (field === 'received' && message.type === 'chat.mode_changed' && message.data?.mode === 'general') {
          lifecycle.sessions.push(message.data.general_chat_id);
        }
      });
    }
  });
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'POST' && (pathname === '/api/workflows/trigger'
      || /^\/api\/chats\/[^/]+\/[^/]+\/start$/.test(pathname))) lifecycle.workflowStarts += 1;
  });
  try {
    const userId = await page.evaluate(async () => (await window.mozaiksAuth.getCurrentUser()).id);
    const restored = page.waitForResponse((response) => response.request().method() === 'GET'
      && response.url().startsWith(`${apiURL}/api/general_chats/transcript/${profile.candidate.app_id}/`));
    await page.getByRole('button', { name: 'Open assistant', exact: true }).click();
    const response = await restored;
    expect(response.status()).toBe(200);
    const transcript = await response.json();
    expect(transcript.found).toBe(true);
    expect(transcript.source).toBe('persistence');
    expect(transcript.app_id).toBe(profile.candidate.app_id);
    expect(transcript.user_id).toBe(userId);
    expect(transcript.messages).toHaveLength(0);
    expect(lifecycle.sessions).toEqual([transcript.chat_id]);
    expect(lifecycle.sent).toContain('chat.enter_general_mode');
    const panel = page.locator('#mozaiks-assistant-panel');
    await expect(panel.getByText('Ask anything', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Minimize', exact: true }).click();
    await expect(panel).toBeHidden();
    await page.getByRole('button', { name: 'Open assistant', exact: true }).click();
    await expect(panel.getByText('Ask anything', { exact: true })).toBeVisible();
    expect(lifecycle.sessions).toEqual([transcript.chat_id]);
    expect(lifecycle.sent).not.toContain('user.input.submit');
    expect(lifecycle.workflowStarts).toBe(0);
    await screenshot(page, testInfo, 'inherited-ask-session-ready');
    await page.getByRole('button', { name: 'Minimize', exact: true }).click();
  } finally {
    await testInfo.attach('assistant-session-lifecycle', { body: JSON.stringify(lifecycle), contentType: 'application/json' });
  }
}

async function exerciseProjects(page, testInfo, label) {
  const main = page.getByRole('main');
  await main.getByRole('button', { name: 'Create project', exact: true }).click();
  const create = page.getByRole('dialog', { name: 'Create project', exact: true });
  await create.getByRole('textbox', { name: /^Project name/ }).fill(label);
  const created = mutationResponse(page, 'projects', 'create_project');
  await create.getByRole('button', { name: 'Create project', exact: true }).click();
  const project = await mutationItem(created, 'project_id');
  await expect(create).toBeHidden();
  await main.getByRole('searchbox').fill(label);
  await selectRecord(page, label);
  await main.getByRole('button', { name: 'Project details', exact: true }).click();
  const detail = page.getByRole('dialog', { name: 'Project details', exact: true });
  await expect(detail.getByRole('textbox', { name: 'Project', exact: true })).toHaveValue(label);
  await expect(detail.getByRole('textbox', { name: 'Project', exact: true })).toBeDisabled();
  await screenshot(page, testInfo, 'project-details');
  await detail.getByRole('button', { name: 'Close', exact: true }).click();
  await page.reload();
  await main.getByRole('searchbox').fill(label);
  await selectRecord(page, label);
  const saved = await moduleAction(page, 'projects', 'get_projects', { id: project.project_id });
  expect(saved.item.name).toBe(label);
  await main.getByRole('button', { name: 'Edit project', exact: true }).click();
  const edit = page.getByRole('dialog', { name: 'Edit project', exact: true });
  const updatedName = `${label} revised`;
  await expect(edit.getByRole('textbox', { name: /^Project name/ })).toHaveValue(label);
  await edit.getByRole('textbox', { name: /^Project name/ }).fill(updatedName);
  await edit.getByRole('combobox', { name: 'Status', exact: true }).click();
  await page.getByRole('option', { name: 'Active', exact: true }).click();
  const updated = mutationResponse(page, 'projects', 'update_project');
  await edit.getByRole('button', { name: 'Save project', exact: true }).click();
  expect(await mutationItem(updated, 'project_id')).toMatchObject({
    project_id: project.project_id, name: updatedName, status: 'active',
  });
  await expect(edit).toBeHidden();
  await page.reload();
  await main.getByRole('searchbox').fill(updatedName);
  await selectRecord(page, updatedName);
  expect((await moduleAction(page, 'projects', 'get_projects', { id: project.project_id })).item)
    .toMatchObject({ name: updatedName, status: 'active' });
  await screenshot(page, testInfo, 'project-edited-after-reload');
  const disposableName = `${label} delete only`;
  const disposable = (await moduleAction(page, 'projects', 'create_project', { name: disposableName })).item;
  await exerciseDeletion(page, testInfo, 'project', disposableName, disposable.project_id);
  expect((await moduleAction(page, 'projects', 'get_projects', { id: project.project_id })).item)
    .toMatchObject({ name: updatedName, status: 'active' });
  return updatedName;
}

async function exerciseTasks(page, testInfo, label) {
  const main = page.getByRole('main');
  const project = (await moduleAction(page, 'projects', 'create_project', { name: `${label} project` })).item;
  const title = `${label} task`;
  const description = 'Created through the declarative form and inspected after a real reload.';
  await main.getByRole('button', { name: 'Create task', exact: true }).click();
  const create = page.getByRole('dialog', { name: 'Create task', exact: true });
  await create.getByRole('textbox', { name: /^Project ID/ }).fill(project.project_id);
  await create.getByRole('textbox', { name: /^Task/ }).fill(title);
  await create.getByRole('combobox', { name: 'Status', exact: true }).click();
  await page.getByRole('option', { name: 'Ready', exact: true }).click();
  await create.getByRole('textbox', { name: 'Details', exact: true }).fill(description);
  const created = mutationResponse(page, 'tasks', 'create_task');
  await create.getByRole('button', { name: 'Create task', exact: true }).click();
  const task = await mutationItem(created, 'task_id');
  await expect(create).toBeHidden();
  await main.getByRole('searchbox').fill(title);
  await selectRecord(page, title);
  await main.getByRole('button', { name: 'View details', exact: true }).click();
  const detail = page.getByRole('dialog', { name: 'Task details', exact: true });
  await expect(detail.getByRole('textbox', { name: /^Task/ })).toHaveValue(title);
  await expect(detail.getByRole('textbox', { name: /^Task/ })).toBeDisabled();
  await expect(detail.getByRole('textbox', { name: 'Details', exact: true })).toHaveValue(description);
  await screenshot(page, testInfo, 'task-read-only-details');
  await detail.getByRole('button', { name: 'Close', exact: true }).click();
  await main.getByRole('button', { name: 'Edit task', exact: true }).click();
  const edit = page.getByRole('dialog', { name: 'Edit task', exact: true });
  const updatedTitle = `${title} revised`;
  await edit.getByRole('textbox', { name: /^Task/ }).fill(updatedTitle);
  await edit.getByRole('combobox', { name: 'Status', exact: true }).click();
  await page.getByRole('option', { name: 'In motion', exact: true }).click();
  const updated = mutationResponse(page, 'tasks', 'update_task');
  await edit.getByRole('button', { name: 'Save task', exact: true }).click();
  expect((await mutationItem(updated, 'task_id')).status).toBe('doing');
  await expect(edit).toBeHidden();
  await page.reload();
  await main.getByRole('searchbox').fill(updatedTitle);
  await selectRecord(page, updatedTitle);
  expect((await moduleAction(page, 'tasks', 'get_tasks', { id: task.task_id })).item)
    .toMatchObject({ title: updatedTitle, status: 'doing', description });

  // ResourceTable pages only its loaded rows. Nine rows prove its local second
  // page; this deliberately makes no claim about fetching beyond the default 20.
  for (let index = 0; index < 8; index += 1) {
    await moduleAction(page, 'tasks', 'create_task', {
      project_id: project.project_id, title: `${label} seed ${index}`, description: 'Bounded paging fixture', status: 'todo',
    });
  }
  await page.reload();
  await main.getByRole('searchbox').fill(label);
  await expect(main.getByText('9 results', { exact: true })).toBeVisible();
  await main.getByRole('button', { name: 'Next page', exact: true }).click();
  await expect(main.getByText('Page 2 of 2', { exact: true })).toBeVisible();
  await expect(main.locator('tr:visible').filter({ has: page.getByRole('radio') })
    .or(main.locator('article:visible'))).toHaveCount(1);
  await screenshot(page, testInfo, 'task-second-loaded-page');
  await main.getByRole('searchbox').fill(updatedTitle);
  await selectRecord(page, updatedTitle);
  await main.getByRole('button', { name: 'View details', exact: true }).click();
  await expect(detail.getByRole('combobox', { name: 'Status', exact: true })).toContainText('In motion');
  await detail.getByRole('button', { name: 'Close', exact: true }).click();
  const disposableTitle = `${label} delete only`;
  const disposable = (await moduleAction(page, 'tasks', 'create_task', {
    project_id: project.project_id, title: disposableTitle, status: 'todo',
  })).item;
  await exerciseDeletion(page, testInfo, 'task', disposableTitle, disposable.task_id);
  expect((await moduleAction(page, 'tasks', 'get_tasks', { id: task.task_id })).item)
    .toMatchObject({ title: updatedTitle, status: 'doing', description });
  await main.getByRole('searchbox').fill(updatedTitle);
  await selectRecord(page, updatedTitle);
}

async function exerciseBoard(page, testInfo, label) {
  const project = (await moduleAction(page, 'projects', 'create_project', { name: `${label} project` })).item;
  const title = `${label} card`;
  const description = 'A real persisted task moved with pointer and keyboard controls.';
  const task = (await moduleAction(page, 'tasks', 'create_task', {
    project_id: project.project_id, title, description, status: 'todo',
  })).item;
  const untouched = (await moduleAction(page, 'tasks', 'create_task', {
    project_id: project.project_id, title: `${label} unchanged`, description: 'Independent record', status: 'todo',
  })).item;
  await page.reload();
  const board = page.getByTestId('planning-board');
  const card = board.getByRole('article').filter({ has: page.getByRole('button', { name: title, exact: true }) });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: title, exact: true }).click();
  const mobilePanel = process.env.MOZAIKS_PAIRED_EXPECT_MOBILE_PANEL === '1'
    && testInfo.project.name === 'generated-mobile';
  const inspector = mobilePanel ? page.getByRole('dialog', { name: title, exact: true })
    : page.getByRole('complementary', { name: 'Task inspector', exact: true });
  async function closeMobileDetails(useEscape = false) {
    if (!mobilePanel) return;
    await expect.poll(async () => {
      const bounds = await inspector.boundingBox();
      return bounds && Math.abs(bounds.y + bounds.height - page.viewportSize().height) <= 1;
    }, { message: 'Refined mobile details must be anchored to the viewport bottom' }).toBe(true);
    if (useEscape) await page.keyboard.press('Escape');
    else await inspector.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(inspector).toBeHidden();
    await expect(card.getByRole('button', { name: title, exact: true })).toBeFocused();
  }
  await expect(inspector.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await expect(inspector.getByText(description, { exact: true })).toBeVisible();
  await screenshot(page, testInfo, mobilePanel ? 'refined-mobile-bottom-panel' : 'board-original-inspector');
  await closeMobileDetails();
  const movingColumn = board.getByRole('region', { name: 'Planning board', exact: true })
    .locator(':scope > div').filter({ has: page.getByRole('heading', { name: /^In motion/ }) });
  const status = board.getByRole('combobox', { name: `Status for ${title}`, exact: true });
  const moved = mutationResponse(page, 'tasks', 'update_task');
  if (testInfo.project.name === 'generated-mobile') {
    // HTML5 drag is not a touch interaction. Mobile uses the shipped native
    // status control; the retained failed mobile drag run remains a limitation.
    await status.selectOption('doing');
    testInfo.annotations.push({ type: 'interaction', description: 'mobile native status control; touch drag not established' });
  } else {
    await card.dragTo(movingColumn.getByText('Drop a card here', { exact: true }));
  }
  expect((await mutationItem(moved, 'task_id')).status).toBe('doing');
  await expect(status).toHaveValue('doing');
  if (mobilePanel) await card.getByRole('button', { name: title, exact: true }).click();
  await expect(inspector.getByText('doing', { exact: true })).toBeVisible();
  await screenshot(page, testInfo, testInfo.project.name === 'generated-mobile'
    ? 'board-native-status-change' : 'board-pointer-move');
  await closeMobileDetails(true);

  // Focus the native select and use its real keyboard interaction; do not
  // synthesize DOM change events or use selectOption for this keyboard check.
  await status.focus();
  await expect(status).toBeFocused();
  const completed = mutationResponse(page, 'tasks', 'update_task');
  await status.press('ArrowDown');
  await status.press('Tab');
  expect((await mutationItem(completed, 'task_id')).status).toBe('done');
  await expect(status).toHaveValue('done');
  await page.reload();
  await expect(status).toHaveValue('done');
  await board.getByRole('button', { name: title, exact: true }).click();
  await expect(inspector.getByText('done', { exact: true })).toBeVisible();
  await expect(inspector.getByText(project.project_id, { exact: true })).toBeVisible();
  expect((await moduleAction(page, 'tasks', 'get_tasks', { id: task.task_id })).item.status).toBe('done');
  expect((await moduleAction(page, 'tasks', 'get_tasks', { id: untouched.task_id })).item.status).toBe('todo');
  await screenshot(page, testInfo, 'board-reloaded-inspector');
  await closeMobileDetails();
  await board.getByRole('link', { name: 'Project details', exact: true }).click();
  await expect(page).toHaveURL(new URL('/projects', baseURL).href);
  await page.getByRole('main').getByRole('searchbox').fill(`${label} project`);
  await selectRecord(page, `${label} project`);
  await screenshot(page, testInfo, 'board-mixed-declarative-route');
  await page.goto(new URL('/board', baseURL).href);
  await expect(status).toHaveValue('done');
}

for (const route of requiredRoutes) {
  test(`paired real-host route ${route.path}`, async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const caseId = `route:${route.path}`;
    expect(profile.cases.some((entry) => entry.id === caseId && entry.route === route.path
      && entry.project === testInfo.project.name), 'Reviewed profile must declare this route and viewport').toBe(true);
    testInfo.annotations.push(
      { type: 'mozaiks.case_id', description: caseId },
      { type: 'mozaiks.route', description: route.path },
      { type: 'mozaiks.run_id', description: runId },
      { type: 'mozaiks.candidate', description: JSON.stringify(profile.candidate) },
    );
    const failures = [];
    page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') failures.push(`console.error: ${message.text()}`);
    });
    const username = testInfo.project.name === 'generated-mobile' ? 'bob' : 'alice';
    const label = `Acceptance ${runId.slice(0, 8)} ${username} ${Date.now()}`;
    try {
      await signIn(page, username, route.visit_path ?? route.path);
      const token = await page.evaluate(() => window.mozaiksAuth.getAccessToken());
      const response = await page.request.get(`${apiURL}/api/shell-config?surface=platform`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect(response.status()).toBe(200);
      const shell = await response.json();
      expect(shell.appId).toBe(profile.candidate.app_id);
      expect(routeInventory(shell.pages)).toEqual(routeInventory(profile.routes));
      await expect(page.getByRole('main').getByRole('heading', { name: route.heading, exact: true }).first()).toBeVisible();
      let projectName = label;
      if (route.path === '/projects') projectName = await exerciseProjects(page, testInfo, label);
      else if (route.path === '/tasks') await exerciseTasks(page, testInfo, label);
      else await exerciseBoard(page, testInfo, label);
      if (route.path === '/projects') await exerciseProfile(page, testInfo, projectName);
      else await exerciseAssistant(page, testInfo);
      await noHorizontalOverflow(page);
      expect(failures, 'Required interactions must not produce browser errors').toEqual([]);
    } finally {
      await screenshot(page, testInfo, `route-${route.path.slice(1)}-${testInfo.project.name}`);
      await testInfo.attach('browser-errors', { body: JSON.stringify(failures, null, 2), contentType: 'application/json' });
    }
  });
}
