/**
 * Screenshots the workflow UI harness at desktop and mobile widths.
 * Usage (from web_shell/, after render-workflow-ui.mjs and a preview server):
 *   node scripts/shoot-workflow-ui.mjs <baseURL> <outDir>
 */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const BASE = process.argv[2] || 'http://127.0.0.1:4173';
const OUT = process.argv[3] || path.join(process.cwd(), 'dist', 'shots');
fs.mkdirSync(OUT, { recursive: true });

const VIEWPORTS = [
  ['desktop', 1440, 900],
  ['mobile', 390, 844],
];

const browser = await chromium.launch();
const report = [];

for (const [label, width, height] of VIEWPORTS) {
  const page = await browser.newPage({ viewport: { width, height } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e.message).slice(0, 160)));
  await page.goto(`${BASE}/harness.html`, { waitUntil: 'networkidle' });
  // Entry animations start at opacity:0 and are flipped by an effect the static
  // harness never runs. Settle them so the screenshot shows the resting state.
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('[style]')) {
      if (el.style.opacity === '0') el.style.opacity = '1';
      if (el.style.transform) el.style.transform = 'none';
      el.style.transitionProperty = 'none';
    }
  });
  await page.waitForTimeout(600);

  await page.screenshot({ path: path.join(OUT, `${label}-all.png`), fullPage: true });

  for (const el of await page.locator('[data-case]').all()) {
    const name = await el.getAttribute('data-case');
    const box = await el.boundingBox();
    await el.screenshot({ path: path.join(OUT, `${label}-${name}.png`) }).catch(() => {});
    // A component wider than the viewport makes the page scroll sideways.
    const overflow = box && box.width > width + 1;
    report.push({ viewport: label, name, height: box ? Math.round(box.height) : null, overflow });
  }

  const docWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  report.push({ viewport: label, name: '__page__', docWidth, overflow: docWidth > width + 1, errors });
  await page.close();
}

await browser.close();
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));

const bad = report.filter((r) => r.overflow);
console.log(bad.length ? `HORIZONTAL OVERFLOW: ${bad.map((b) => `${b.viewport}/${b.name}`).join(', ')}` : 'no horizontal overflow');
for (const r of report.filter((r) => r.name === '__page__')) {
  console.log(`${r.viewport}: docWidth=${r.docWidth} pageErrors=${r.errors.length ? r.errors.join(' | ') : 'none'}`);
}
console.log(`shots -> ${OUT}`);
