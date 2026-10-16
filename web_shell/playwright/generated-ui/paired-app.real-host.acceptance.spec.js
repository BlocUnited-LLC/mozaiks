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
if (!['/board,/messages,/projects', '/messages,/projects,/tasks'].includes(paths.join(','))) {
  throw new Error('Paired acceptance requires /messages, /projects and exactly one of /tasks or /board.');
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

async function exerciseMessages(page, testInfo, label, username) {
  const title = `${label} private conversation`;
  const body = `${label} persisted message`;
  const actor = await page.evaluate(async () => (await window.mozaiksAuth.getCurrentUser()).id);
  await page.getByPlaceholder('Thread title', { exact: true }).fill(title);
  const created = mutationResponse(page, 'messages', 'create_thread');
  await page.getByRole('button', { name: 'Start conversation', exact: true }).click();
  const createdResponse = await created;
  expect(createdResponse.status()).toBe(200);
  const result = await createdResponse.json();
  expect(result.success).toBe(true);
  const thread = result.thread;
  expect(thread.thread_id).toEqual(expect.any(String));
  expect(thread.participant_ids).toEqual([actor]);
  expect(thread.scope_type).toBe('app');
  expect(thread.scope_id).toBe(profile.candidate.app_id);
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await page.getByPlaceholder('Type a message', { exact: true }).fill(body);
  const sent = mutationResponse(page, 'messages', 'send_message');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const sentResponse = await sent;
  expect(sentResponse.status()).toBe(200);
  const sentBody = await sentResponse.json();
  expect(sentBody.success).toBe(true);
  expect(sentBody.message.sender_id).toBe(actor);
  expect(sentBody.message.body).toBe(body);
  await expect(page.locator('article').getByText(body, { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button').filter({ has: page.getByText(title, { exact: true }) }).click();
  await expect(page.locator('article').getByText(body, { exact: true })).toBeVisible();
  const persisted = await moduleAction(page, 'messages', 'get_thread', { thread_id: thread.thread_id });
  expect(persisted.messages.map((message) => message.message_id)).toEqual([sentBody.message.message_id]);
  await noHorizontalOverflow(page);
  await screenshot(page, testInfo, 'pack-messages-persisted-after-reload');

  // A second real PKCE session has the same public scopes but is not a thread
  // participant. The pack's private-miss contract returns empty/false results.
  const outsiderContext = await page.context().browser().newContext({ viewport: page.viewportSize() });
  let privateEvidence;
  try {
    const outsider = await outsiderContext.newPage();
    await signIn(outsider, username === 'alice' ? 'bob' : 'alice', '/messages');
    await expect(outsider.getByRole('heading', { name: 'Messages', exact: true })).toBeVisible();
    const outsiderId = await outsider.evaluate(async () => (await window.mozaiksAuth.getCurrentUser()).id);
    expect(outsiderId).not.toBe(actor);
    const listed = await moduleAction(outsider, 'messages', 'list_threads', { limit: 50 });
    expect(listed.threads.some((entry) => entry.thread_id === thread.thread_id)).toBe(false);
    await expect(outsider.getByText(title, { exact: true })).toHaveCount(0);
    const read = await moduleAction(outsider, 'messages', 'get_thread', { thread_id: thread.thread_id });
    expect(read.thread).toBeNull();
    expect(read.messages).toEqual([]);
    const write = await moduleAction(outsider, 'messages', 'send_message', {
      thread_id: thread.thread_id, body: `${label} forbidden message`,
    });
    expect(write.success).toBe(false);
    const mark = await moduleAction(outsider, 'messages', 'mark_thread_read', { thread_id: thread.thread_id });
    expect(mark.success).toBe(false);
    const anonymous = await outsider.request.post(`${apiURL}/api/modules/messages/list_threads`, {
      data: { params: { limit: 50 } },
    });
    expect([401, 403]).toContain(anonymous.status());
    await noHorizontalOverflow(outsider);
    await screenshot(outsider, testInfo, 'pack-messages-private-for-other-member');
    privateEvidence = { hidden_from_list: true, unreadable: true, write_denied: true,
      mark_read_denied: true, anonymous_status: anonymous.status() };
  } finally {
    await outsiderContext.close();
  }
  const unchanged = await moduleAction(page, 'messages', 'get_thread', { thread_id: thread.thread_id });
  expect(unchanged.messages.map((message) => message.message_id)).toEqual([sentBody.message.message_id]);
  let normalizedEvidence = {};
  if (process.env.MOZAIKS_PAIRED_EXPECT_NORMALIZED_MESSAGE === '1') {
    // The accepted backend refinement's handler extension collapses whitespace
    // before the pack service validates, authorizes and stores the body.
    const spaced = await moduleAction(page, 'messages', 'send_message', {
      thread_id: thread.thread_id, body: `${label}   spaced \t reply`,
    });
    expect(spaced.success).toBe(true);
    expect(spaced.message.body).toBe(`${label} spaced reply`);
    const stored = await moduleAction(page, 'messages', 'get_thread', { thread_id: thread.thread_id });
    expect(stored.messages.find((message) => message.message_id === spaced.message.message_id)?.body)
      .toBe(`${label} spaced reply`);
    normalizedEvidence = { normalized_message_id: spaced.message.message_id, normalized_body: spaced.message.body };
  }
  await testInfo.attach('pack-messaging-persistence-and-private-scope', {
    body: JSON.stringify({ thread_id: thread.thread_id, message_id: sentBody.message.message_id,
      scope_type: thread.scope_type, scope_id: thread.scope_id, ...privateEvidence, ...normalizedEvidence }),
    contentType: 'application/json',
  });
}

async function exerciseServerPaging(page, testInfo, { entity, label, projectId, board = false }) {
  const moduleId = `${entity}s`;
  const endpoint = `/api/modules/${moduleId}/list_${moduleId}`;
  const identity = `${entity}_id`;
  const prefix = `${label} paging`;
  const literal = `${prefix} literal [x].* &? +`;
  const records = [];
  // The target is the 25th record: beyond both the explicit eight-row page and
  // the canonical action's default 20-row first batch.
  for (let index = 0; index < 25; index += 1) {
    const name = index === 24 ? literal : `${prefix} ${String(index).padStart(2, '0')}`;
    records.push((await moduleAction(page, moduleId, `create_${entity}`, entity === 'project'
      ? { name }
      : { project_id: projectId, title: name, description: 'Server paging acceptance', status: 'todo' })).item);
  }
  function inputs(request) {
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      return body.params ?? body;
    }
    const query = new URL(request.url()).searchParams;
    return { page: Number(query.get('page')), page_size: Number(query.get('page_size')), search: query.get('search') };
  }
  const requests = [];
  const responses = [];
  const observe = (request) => {
    if (new URL(request.url()).pathname === endpoint) requests.push({ method: request.method(), input: inputs(request) });
  };
  const scope = board ? page.getByTestId('planning-board') : page.getByRole('main');
  const search = scope.getByRole('searchbox');
  const visibleRecords = scope.locator('tr:visible, article:visible').filter({
    has: board ? page.getByRole('combobox') : page.getByRole('radio'),
  });
  async function readPage(action, number, phrase, expectedRecords) {
    const expectedQuery = { page: number, page_size: 8, search: phrase };
    const pending = page.waitForResponse((response) => {
      if (new URL(response.url()).pathname !== endpoint) return false;
      const query = inputs(response.request());
      return query.page === number && query.page_size === 8 && query.search === phrase;
    });
    await action();
    const response = await pending;
    expect(response.status(), 'The UI must retrieve the actual bounded server page').toBe(200);
    expect(response.request().method()).toBe(board ? 'POST' : 'GET');
    expect(inputs(response.request())).toEqual(expectedQuery);
    const body = await response.json();
    const expectedTotal = phrase === literal ? 1 : records.length;
    expect(body.total).toBe(expectedTotal);
    expect(body.items.map((item) => item[identity])).toEqual(expectedRecords.map((item) => item[identity]));
    expect(body.items.length).toBeLessThanOrEqual(8);
    await expect(scope.getByText(`${body.total} results`, { exact: true })).toBeVisible();
    await expect(scope.getByText(`Page ${number} of ${Math.max(1, Math.ceil(body.total / 8))}`, { exact: true })).toBeVisible();
    await expect(visibleRecords).toHaveCount(expectedRecords.length);
    responses.push({ query: expectedQuery, total: body.total, ids: body.items.map((item) => item[identity]) });
    return body;
  }
  page.on('request', observe);
  try {
    await page.reload();
    const first = await readPage(() => search.fill(prefix), 1, prefix, records.slice(0, 8));
    expect(first.items.some((item) => item[identity] === records[24][identity])).toBe(false);
    for (const number of [2, 3, 4]) {
      await readPage(() => scope.getByRole('button', { name: 'Next page', exact: true }).click(),
        number, prefix, records.slice((number - 1) * 8, number * 8));
    }
    await expect(visibleRecords.getByText(literal, { exact: true })).toBeVisible();
    await screenshot(page, testInfo, `${entity}-${board ? 'board' : 'table'}-server-fourth-page`);
    await readPage(() => search.fill(literal), 1, literal, [records[24]]);
    await expect(visibleRecords.getByText(literal, { exact: true })).toBeVisible();
    await expect(scope.getByRole('button', { name: 'Next page', exact: true })).toBeDisabled();
    await screenshot(page, testInfo, `${entity}-${board ? 'board' : 'table'}-literal-server-search`);
    expect(requests.length).toBeGreaterThanOrEqual(5);
    for (const request of requests) {
      expect(request.method).toBe(board ? 'POST' : 'GET');
      expect(request.input.page_size, 'Every UI request stays bounded; no load-all workaround').toBe(8);
      expect(Number.isSafeInteger(request.input.page) && request.input.page >= 1).toBe(true);
      expect(typeof request.input.search).toBe('string');
    }
  } finally {
    page.off('request', observe);
    await testInfo.attach(`${entity}-${board ? 'board' : 'table'}-server-pages`, {
      body: JSON.stringify({ requests, responses }), contentType: 'application/json',
    });
  }
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
  await exerciseServerPaging(page, testInfo, { entity: 'project', label });
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

  await exerciseServerPaging(page, testInfo, { entity: 'task', label, projectId: project.project_id });
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
  await board.getByRole('searchbox').fill(title);
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
  await board.getByRole('searchbox').fill(title);
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
  await board.getByRole('searchbox').fill(title);
  await expect(status).toHaveValue('done');
  await exerciseServerPaging(page, testInfo, { entity: 'task', label, projectId: project.project_id, board: true });
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
      else if (route.path === '/messages') await exerciseMessages(page, testInfo, label, username);
      else await exerciseBoard(page, testInfo, label);
      if (route.path === '/projects') await exerciseProfile(page, testInfo, projectName);
      else if (route.path !== '/messages') await exerciseAssistant(page, testInfo);
      await noHorizontalOverflow(page);
      expect(failures, 'Required interactions must not produce browser errors').toEqual([]);
    } finally {
      await screenshot(page, testInfo, `route-${route.path.slice(1)}-${testInfo.project.name}`);
      await testInfo.attach('browser-errors', { body: JSON.stringify(failures, null, 2), contentType: 'application/json' });
    }
  });
}
