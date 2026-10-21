import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

// These checks require actual platform, Mongo and Keycloak services. The stale
// refresh check delays an actual server response; no records or identity replies
// are fabricated by Playwright.
const configPath = resolve(process.env.COMMUNITY_LIVE_CONFIG);
const environment = JSON.parse(readFileSync(configPath, 'utf8'));
const user = name => environment.users.find(entry => entry.username === name);

async function signIn(page, username, path = '/community') {
  await page.goto(`${environment.base_url}${path}`);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${environment.auth_issuer}/protocol/openid-connect/auth`));
  await page.getByLabel('Username or email', { exact: true }).fill(username);
  await page.getByLabel('Password', { exact: true }).fill(user(username).password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await expect(page).toHaveURL(`${environment.base_url}${path}`);
  await expect.poll(() => page.evaluate(async () => Boolean((await window.mozaiksAuth?.getCurrentUser())?.id))).toBe(true);
}

async function action(page, name, params) {
  const token = await page.evaluate(() => window.mozaiksAuth.getAccessToken());
  return page.request.post(`${environment.api_url}/api/modules/user_posts/${name}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { params },
  });
}

async function readPost(page, postId) {
  const response = await action(page, 'get_post', { post_id: postId });
  expect(response.status()).toBe(200);
  return (await response.json()).post;
}

async function noHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

async function holdRealResponse(page, url) {
  let release;
  const gate = new Promise(resolveGate => { release = resolveGate; });
  await page.route(url, async route => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  }, { times: 1 });
  return release;
}

test('members post, comment and react; ownership and data survive reload and backend restart', async ({ page, browser }, testInfo) => {
  const browserErrors = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  const memberContext = await browser.newContext(testInfo.project.use);
  const memberPage = await memberContext.newPage();
  memberPage.on('pageerror', error => browserErrors.push(error.message));
  const body = `A place to make things together — ${testInfo.project.name} ${Date.now()}`;
  const reply = 'I would love to help with the next community project.';
  let postId;
  const releaseFeed = await holdRealResponse(page, '**/api/modules/user_posts/list_posts');
  const releaseComments = await holdRealResponse(memberPage, '**/api/modules/user_posts/list_comments');
  try {
    await signIn(page, 'alice');
    await expect(page.getByText('Loading conversations…', { exact: true })).toBeVisible();
    releaseFeed();
    await expect(page.getByText('Every community starts with a hello.', { exact: true })).toBeVisible();
    const token = await page.evaluate(() => window.mozaiksAuth.getAccessToken());
    const workflows = await page.request.get(`${environment.api_url}/api/workflows`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(workflows.status()).toBe(200);
    expect(await workflows.json()).toEqual({ workflows: [] });
    await page.getByLabel('What would you like to share?', { exact: true }).fill(body);
    const createdPromise = page.waitForResponse(response => response.url().endsWith('/user_posts/create_post') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Share post', exact: true }).click();
    const created = await createdPromise;
    expect(created.status()).toBe(200);
    const createdBody = await created.json();
    expect(createdBody.success).toBe(true);
    postId = createdBody.post.post_id;
    const post = page.getByTestId(`post-${postId}`);
    await expect(post.getByText(body, { exact: true })).toBeVisible();
    await noHorizontalOverflow(page);

    await signIn(memberPage, 'bob', `/community/${postId}`);
    await expect(memberPage.getByText(body, { exact: true })).toBeVisible();
    await expect(memberPage.getByText('Loading the conversation…', { exact: true })).toBeVisible();
    releaseComments();
    await expect(memberPage.getByText('There is room for your perspective.', { exact: true })).toBeVisible();
    await expect(memberPage.getByRole('button', { name: 'Delete your post', exact: true })).toHaveCount(0);
    await memberPage.getByRole('button', { name: 'Like post', exact: true }).click();
    await expect(memberPage.getByRole('button', { name: 'Unlike post', exact: true })).toBeVisible();
    await memberPage.getByLabel('Add to the conversation', { exact: true }).fill(reply);
    const commentPromise = memberPage.waitForResponse(response => response.url().endsWith('/user_posts/add_comment') && response.request().method() === 'POST');
    await memberPage.getByRole('button', { name: 'Post comment', exact: true }).click();
    const commentBody = await (await commentPromise).json();
    expect(commentBody.success).toBe(true);
    const commentId = commentBody.comment.comment_id;
    await expect(memberPage.getByTestId(`comment-${commentId}`).getByText(reply, { exact: true })).toBeVisible();

    const deniedPost = await action(memberPage, 'delete_post', { post_id: postId });
    expect(deniedPost.status()).toBe(200);
    expect((await deniedPost.json()).success).toBe(false);
    const deniedComment = await action(page, 'delete_comment', { comment_id: commentId });
    expect(deniedComment.status()).toBe(200);
    expect((await deniedComment.json()).success).toBe(false);
    expect((await readPost(page, postId)).body).toBe(body);

    const anonymous = await page.request.post(`${environment.api_url}/api/modules/user_posts/create_post`, { data: { params: { body: 'unauthorized' } } });
    expect(anonymous.status()).toBe(403);
    for (const visibility of ['private', 'friends']) {
      const rejected = await action(page, 'create_post', { body: 'Not admitted by this reference', visibility });
      expect(rejected.status()).toBe(400);
    }
    const spoofed = await action(memberPage, 'delete_post', { post_id: postId, user_id: createdBody.post.author_id });
    expect(spoofed.status()).toBe(403);

    await memberPage.reload();
    await expect(memberPage.getByRole('button', { name: 'Unlike post', exact: true })).toBeVisible();
    await expect(memberPage.getByText(reply, { exact: true })).toBeVisible();
    const beforeRestart = await readPost(page, postId);
    expect(beforeRestart.reaction_count).toBe(1);
    expect(beforeRestart.comment_count).toBe(1);

    // Restart only the owned acceptance API process, preserving the actual DB.
    // A service restart does not qualify native lifecycle or distributed failover.
    execFileSync(process.env.COMMUNITY_PYTHON || 'python', [
      resolve('../examples/canonical-apps/community/scripts/live_environment.py'),
      'restart-api', '--evidence-dir', dirname(configPath),
    ], { cwd: process.cwd(), timeout: 90000, stdio: 'pipe' });
    await memberPage.reload();
    await expect(memberPage.getByText(body, { exact: true })).toBeVisible();
    await expect(memberPage.getByText(reply, { exact: true })).toBeVisible();
    expect(await readPost(page, postId)).toEqual(beforeRestart);
    await noHorizontalOverflow(memberPage);
    await memberPage.screenshot({ path: testInfo.outputPath('community-thread.png'), fullPage: true });

    await memberPage.getByTestId(`comment-${commentId}`).getByRole('button', { name: 'Delete your comment', exact: true }).click();
    await memberPage.getByRole('dialog').getByRole('button', { name: 'Delete comment', exact: true }).click();
    await expect(memberPage.getByText(reply, { exact: true })).toHaveCount(0);
    await memberPage.getByRole('button', { name: 'Unlike post', exact: true }).click();
    await expect(memberPage.getByRole('button', { name: 'Like post', exact: true })).toBeVisible();

    await page.goto(`${environment.base_url}/community/${postId}`);
    await page.getByRole('button', { name: 'Delete your post', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete post', exact: true }).click();
    await expect(page).toHaveURL(`${environment.base_url}/community`);
    expect(await readPost(page, postId)).toBeNull();
    await memberPage.reload();
    await expect(memberPage.getByText(body, { exact: true })).toHaveCount(0);
    await expect(memberPage.getByText('This post is no longer available.', { exact: true })).toBeVisible();
    expect(browserErrors).toEqual([]);
  } finally {
    releaseFeed();
    releaseComments();
    // Clean only this test's synthetic post if an assertion interrupted the UI.
    if (postId) await action(page, 'delete_post', { post_id: postId }).catch(() => {});
    await memberContext.close();
  }
});

test('failed post preserves its draft and can be retried after connectivity returns', async ({ page, context }, testInfo) => {
  await signIn(page, 'alice');
  const draft = `An idea worth keeping — ${testInfo.project.name} ${Date.now()}`;
  await page.getByLabel('What would you like to share?', { exact: true }).fill(draft);
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Refresh community', exact: true }).click();
  await expect(page.getByText('The community is unavailable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Share post', exact: true }).click();
  await expect(page.getByRole('form', { name: 'Write a post' }).getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('What would you like to share?', { exact: true })).toHaveValue(draft);
  await page.screenshot({ path: testInfo.outputPath('community-offline-draft.png'), fullPage: true });
  await context.setOffline(false);
  await page.getByRole('button', { name: 'Retry loading', exact: true }).click();
  await expect(page.getByText('The community is unavailable', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('What would you like to share?', { exact: true })).toHaveValue(draft);
  const createdPromise = page.waitForResponse(response => response.url().endsWith('/user_posts/create_post') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Share post', exact: true }).click();
  const created = await (await createdPromise).json();
  expect(created.success).toBe(true);
  await expect(page.getByTestId(`post-${created.post.post_id}`).getByText(draft, { exact: true })).toBeVisible();
  await expect(page.getByLabel('What would you like to share?', { exact: true })).toHaveValue('');
  await page.screenshot({ path: testInfo.outputPath('community-feed.png'), fullPage: true });
  await noHorizontalOverflow(page);
  await action(page, 'delete_post', { post_id: created.post.post_id });
});

test('a new reply stays visible while older comment pages are loaded', async ({ page }, testInfo) => {
  await signIn(page, 'alice');
  const created = await (await action(page, 'create_post', { body: `A longer conversation — ${testInfo.project.name}` })).json();
  expect(created.success).toBe(true);
  const postId = created.post.post_id;
  try {
    for (let index = 1; index <= 51; index += 1) {
      const seeded = await (await action(page, 'add_comment', { post_id: postId, body: `Earlier reply ${index}` })).json();
      expect(seeded.success).toBe(true);
    }
    await page.route('**/api/modules/user_posts/list_comments', route => route.abort('connectionfailed'), { times: 1 });
    await page.goto(`${environment.base_url}/community/${postId}`);
    await expect(page.getByText('Could not load comments', { exact: true })).toBeVisible();
    await page.getByLabel('Add to the conversation', { exact: true }).fill('My newest reply');
    await page.getByRole('button', { name: 'Retry comments', exact: true }).click();
    await expect(page.getByText('Earlier reply 50', { exact: true })).toBeVisible();
    await expect(page.getByText('Could not load comments', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Earlier reply 51', { exact: true })).toHaveCount(0);
    await expect(page.getByLabel('Add to the conversation', { exact: true })).toHaveValue('My newest reply');
    const posted = page.waitForResponse(response => response.url().endsWith('/user_posts/add_comment'));
    await page.getByRole('button', { name: 'Post comment', exact: true }).click();
    const reply = await (await posted).json();
    expect(reply.success).toBe(true);
    await expect(page.getByTestId(`comment-${reply.comment.comment_id}`)).toBeVisible();
    await page.getByRole('button', { name: 'More comments', exact: true }).click();
    await expect(page.getByText('Earlier reply 51', { exact: true })).toBeVisible();
    const comments = page.locator('[data-comment-id]');
    await expect(comments).toHaveCount(52);
    await expect(comments.nth(50)).toContainText('Earlier reply 51');
    await expect(comments.nth(51)).toContainText('My newest reply');
    await expect(page.getByLabel('Add to the conversation', { exact: true })).toHaveValue('');
    await comments.first().getByRole('button', { name: 'Delete your comment', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete comment', exact: true }).click();
    await expect(comments).toHaveCount(51);
    await expect(comments.last()).toContainText('My newest reply');
  } finally {
    await action(page, 'delete_post', { post_id: postId });
  }
});

test('a delayed real feed response cannot put a confirmed deletion back on screen', async ({ page }, testInfo) => {
  await signIn(page, 'alice');
  const created = await (await action(page, 'create_post', { body: `Remove this conversation — ${testInfo.project.name}` })).json();
  expect(created.success).toBe(true);
  const postId = created.post.post_id;
  await page.reload();
  const post = page.getByTestId(`post-${postId}`);
  await expect(post).toBeVisible();
  let release;
  let captured;
  const responseGate = new Promise(resolveGate => { release = resolveGate; });
  const serverResponded = new Promise(resolveCaptured => { captured = resolveCaptured; });
  const feedUrl = '**/api/modules/user_posts/list_posts';
  await page.route(feedUrl, async route => {
    const response = await route.fetch();
    captured();
    await responseGate;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole('button', { name: 'Refresh community', exact: true }).click();
    await serverResponded;
    await post.getByRole('button', { name: 'Delete your post', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete post', exact: true }).click();
    await expect(post).toHaveCount(0);
    const delivered = page.waitForResponse(response => response.url().endsWith('/user_posts/list_posts'));
    release();
    await (await delivered).finished();
    // Let the delivered fetch and React's next render reach the screen.
    await page.evaluate(() => new Promise(resolveFrame => requestAnimationFrame(() => requestAnimationFrame(resolveFrame))));
    await expect(post).toHaveCount(0);
    expect(await readPost(page, postId)).toBeNull();
  } finally {
    release();
    await page.unroute(feedUrl);
    await action(page, 'delete_post', { post_id: postId });
  }
});
