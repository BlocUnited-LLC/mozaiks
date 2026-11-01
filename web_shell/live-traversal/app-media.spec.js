// Live AppMediaGenerator smoke: real LLM and image calls against a running Studio.
//
// 1. Seed a registered build whose bundle plans images:
//      python scripts/seed_app_media_smoke.py --stage-dir <dir>   -> prints JSON
// 2. Run with that JSON:
//      MEDIA_SMOKE_SEED='<json>' MEDIA_SMOKE_PYTHON=<python> STUDIO_URL=http://localhost:3000 \
//        npx playwright test -c live-traversal/app-media.config.js
//
// Passes when the chat shows the generated images and the build's current
// bundle becomes a child version that carries them.
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const HOST_APP_ID = process.env.MEDIA_SMOKE_HOST_APP_ID || 'mozaiks-factory';
const USER_ID = process.env.MEDIA_SMOKE_USER_ID || 'anonymous';

function seed() {
  const raw = process.env.MEDIA_SMOKE_SEED;
  if (!raw) throw new Error('MEDIA_SMOKE_SEED must hold the JSON printed by scripts/seed_app_media_smoke.py');
  return JSON.parse(raw);
}

function verify(buildRegistryId) {
  const python = process.env.MEDIA_SMOKE_PYTHON || 'python';
  const out = execFileSync(
    python,
    [path.join(REPO_ROOT, 'scripts', 'seed_app_media_smoke.py'), '--verify', buildRegistryId],
    { cwd: REPO_ROOT, encoding: 'utf-8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } },
  );
  return JSON.parse(out.trim().split('\n').pop());
}

test('AppMediaGenerator makes the planned images and publishes them into a new bundle version', async ({ page, request }) => {
  const seeded = seed();
  const frames = [];
  page.on('websocket', (ws) => ws.on('framereceived', (frame) => {
    if (typeof frame.payload === 'string') frames.push(frame.payload);
  }));

  await expect.poll(async () => (await request.get('/api/shell-config')).status(), { timeout: 120_000 }).toBe(200);

  const start = await request.post(`/api/chats/${HOST_APP_ID}/AppMediaGenerator/start`, {
    data: { user_id: USER_ID, build_registry_id: seeded.build_registry_id, force_new: true },
  });
  expect(start.status(), await start.text()).toBeLessThan(300);
  const started = await start.json();
  expect(started.workflow_name ?? 'AppMediaGenerator').toBe('AppMediaGenerator');
  const chatId = started.chat_id;
  expect(chatId).toBeTruthy();

  await page.goto(`/chat?workflow=AppMediaGenerator&mode=workflow&app_id=${HOST_APP_ID}&chat_id=${chatId}`);

  // Each image the batch makes is harvested and announced as a generated-media artifact.
  const mediaFrames = () => frames.filter((f) => f.includes('core.media.generated_asset'));
  await expect.poll(() => mediaFrames().length, { timeout: 10 * 60_000, intervals: [5_000] })
    .toBeGreaterThanOrEqual(2);

  const preview = page.locator('img[src*="/api/media/assets/"]').first();
  await expect(preview).toBeVisible({ timeout: 120_000 });
  await expect.poll(() => preview.evaluate((img) => img.naturalWidth), { timeout: 60_000 }).toBeGreaterThan(0);
  const src = await preview.getAttribute('src');
  const content = await request.get(src);
  expect(content.status()).toBe(200);
  expect(content.headers()['content-type']).toMatch(/^image\//);

  // The publisher moves the build to a child version that carries the images.
  await expect.poll(() => verify(seeded.build_registry_id).source_workflow, {
    timeout: 5 * 60_000, intervals: [5_000],
  }).toBe('AppMediaGenerator');
  const published = verify(seeded.build_registry_id);
  test.info().annotations.push({ type: 'published', description: JSON.stringify(published) });

  expect(published.parent_build_record_id).toBe(seeded.artifact_version_id);
  expect(published.lifecycle_status).toMatch(/draft/i);
  expect(published.validation_status).toMatch(/passed/i);
  expect(published.blocking_diagnostics).toEqual([]);
  const byId = Object.fromEntries(published.assets.map((asset) => [asset.asset_id, asset]));
  for (const assetId of ['home-hero', 'menu-illustration']) {
    expect(byId[assetId].path).toMatch(new RegExp(`^ui/assets/${assetId}\\.(webp|png|jpg)$`));
    expect(published.media_files[byId[assetId].path]).toBeGreaterThan(10_000);
  }
  expect(byId['signature-latte-photo'].path).toBe('ui/assets/placeholders/signature-latte-photo.svg');
  expect(byId['signature-latte-photo'].tags).toContain('placeholder');

  await expect(page.getByText(/Photo to upload|upload|placeholder/i).first()).toBeVisible({ timeout: 120_000 });
  await page.screenshot({ path: test.info().outputPath('app-media-chat.png'), fullPage: true });
});
