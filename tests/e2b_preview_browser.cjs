const { chromium } = require(process.argv[2]);
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await fs.mkdir(process.argv[4], { recursive: true });
    const evidence = [];
    for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      const loadedFonts = [];
      page.on('pageerror', error => errors.push(error.message));
      // Prove the bundled font works even when a third-party font CDN is unavailable.
      await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, route => route.abort());
      page.on('response', response => {
        if (response.request().resourceType() === 'font' && response.ok()) loadedFonts.push(response.url());
      });
      await page.goto(process.argv[3], { waitUntil: 'domcontentloaded' });
      console.log(`${name} initial UI:`, await page.locator('body').innerText());
      try {
        await page.getByRole('heading', { name: 'Reports', exact: true }).waitFor({ timeout: 30000 });
        const reports = name === 'desktop' ? page.getByRole('table') : page.locator('article');
        await reports.getByText('Readiness', { exact: true }).waitFor({ timeout: 30000 });
        assert.equal(new URL(page.url()).pathname, '/reports', 'The app entry must open its public landing page');
        await page.getByRole('searchbox', {name: 'Search...', exact: true}).fill('no matching report');
        await reports.getByText('Readiness', {exact: true}).waitFor({state: 'hidden'});
        await page.getByRole('searchbox', {name: 'Search...', exact: true}).fill('');
        await reports.getByText('Readiness', {exact: true}).waitFor();
        await page.getByText('Deterministic Reports', { exact: true }).first().waitFor({ timeout: 30000 });
        await page.getByRole('note', { name: 'Draft app preview' }).waitFor();
        await page.waitForFunction(() => getComputedStyle(document.body).fontFamily.includes('Oxanium'));
        await page.waitForFunction(() => [...document.styleSheets].some(sheet => {
          try { return [...sheet.cssRules].some(rule => rule.cssText.includes('/fonts/Oxanium-VariableFont_wght.ttf')); }
          catch { return false; }
        }));
        await page.evaluate(() => document.fonts.load('16px "Oxanium"'));
        await page.evaluate(() => document.fonts.ready);
        assert(await page.evaluate(() => [...document.fonts].some(font =>
          font.family.replaceAll('"', '').replaceAll("'", '') === 'Oxanium' && font.status === 'loaded'
        )), 'The app-owned font must load, not just appear in the CSS font stack');
        assert(loadedFonts.some(url => url === `${process.argv[3]}/fonts/Oxanium-VariableFont_wght.ttf`),
          'The browser must load the font file from the app preview');
        await page.waitForFunction(() => Array.from(document.images).every(img => {
          const rect = img.getBoundingClientRect();
          const inViewport = rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
          return !inViewport || !img.checkVisibility({ opacityProperty: true, visibilityProperty: true })
            || (img.complete && img.naturalWidth > 0);
        }));
      } catch (error) {
        console.log(`${name} failed UI:`, await page.locator('body').innerText(), errors);
        console.log('Brand diagnostics:', await page.evaluate(() => ({
          body: getComputedStyle(document.body).fontFamily,
          primary: getComputedStyle(document.documentElement).getPropertyValue('--color-primary'),
          fonts: [...document.fonts].map(font => ({ family: font.family, status: font.status })),
        })));
        console.log('Loaded font requests:', loadedFonts);
        console.log('Image diagnostics:', await page.locator('img').evaluateAll(images => images.map(img => ({
          src: img.src, visible: img.checkVisibility({ opacityProperty: true, visibilityProperty: true }),
          complete: img.complete, naturalWidth: img.naturalWidth, rect: img.getBoundingClientRect().toJSON(),
        }))));
        await page.screenshot({ path: path.join(process.argv[4], `${name}-failed.png`), fullPage: true });
        throw error;
      }
      await page.screenshot({ path: path.join(process.argv[4], `${name}.png`), fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      if (overflow || errors.length) throw new Error(JSON.stringify({ name, overflow, errors }));
      const theme = await page.evaluate(() => ({
        primary: getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim(),
        bodyFont: getComputedStyle(document.body).fontFamily,
        background: getComputedStyle(document.body).backgroundColor,
      }));
      assert.equal(theme.primary.toLowerCase(), '#0f766e', 'Preview must retain its own teal brand');
      assert.equal(theme.background, 'rgb(244, 248, 252)', 'The app light appearance must not inherit a dark shell');
      const result = { name, renderedReport: true, publicLanding: true, searchWorks: true, draftPreview: true, localFontLoaded: true, ...theme, overflow, errors };
      evidence.push(result);
      console.log(JSON.stringify(result));
      await page.close();
    }
    await fs.writeFile(path.join(process.argv[4], 'browser-results.json'), JSON.stringify(evidence, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
