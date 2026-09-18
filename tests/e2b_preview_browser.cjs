const { chromium } = require(process.argv[2]);
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await fs.mkdir(process.argv[4], { recursive: true });
    for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${process.argv[3]}/reports`, { waitUntil: 'domcontentloaded' });
      console.log(`${name} initial UI:`, await page.locator('body').innerText());
      try {
        await page.getByRole('heading', { name: 'Reports', exact: true }).waitFor({ timeout: 30000 });
        const reports = name === 'desktop' ? page.getByRole('table') : page.locator('article');
        await reports.getByText('Readiness', { exact: true }).waitFor({ timeout: 30000 });
      } catch (error) {
        console.log(`${name} failed UI:`, await page.locator('body').innerText(), errors);
        await page.screenshot({ path: path.join(process.argv[4], `${name}-failed.png`), fullPage: true });
        throw error;
      }
      await page.screenshot({ path: path.join(process.argv[4], `${name}.png`), fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      if (overflow || errors.length) throw new Error(JSON.stringify({ name, overflow, errors }));
      console.log(JSON.stringify({ name, renderedReport: true, overflow, errors }));
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
