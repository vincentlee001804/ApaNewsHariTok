// One-off dev probe: load SEB Cares portal, click around, log all XHR/fetch
// requests so we can find the announcements/outages JSON API endpoint.
// Usage: node tools/seb_network_probe.js
const { chromium } = require('playwright');

const BASE = 'https://sebcares.sarawakenergy.com';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 900 },
  });
  const page = await ctx.newPage();

  const apiCalls = [];
  page.on('response', async (res) => {
    const url = res.url();
    const ct = (res.headers()['content-type'] || '').toLowerCase();
    if (
      res.request().resourceType() === 'xhr' ||
      res.request().resourceType() === 'fetch' ||
      ct.includes('json')
    ) {
      let preview = '';
      try {
        const text = await res.text();
        preview = text.slice(0, 300).replace(/\s+/g, ' ');
      } catch (e) {
        preview = '<body unreadable>';
      }
      apiCalls.push({ status: res.status(), url, preview });
    }
  });

  console.log('Loading portal...');
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(12000);

  // Dump visible nav/menu text to find the outages/announcements entry point.
  const bodyText = await page.evaluate(() => document.body.innerText || '');
  console.log('=== PAGE TEXT (first 2000 chars) ===');
  console.log(bodyText.slice(0, 2000));

  // Try clicking any link/button that mentions outage, announcement, notice.
  const keywords = /outage|announce|notice|interruption|supply/i;
  const candidates = await page.$$eval('a, button, [role="button"]', (els) =>
    els.map((e) => ({ tag: e.tagName, text: (e.innerText || '').trim(), href: e.href || '' }))
  );
  console.log('=== CLICKABLE ELEMENTS ===');
  for (const c of candidates.filter((c) => c.text)) {
    console.log(`${c.tag} | ${c.text.slice(0, 80)} | ${c.href}`);
  }

  const targets = candidates.filter((c) => keywords.test(c.text));
  for (const t of targets.slice(0, 5)) {
    console.log(`\n>>> Clicking: ${t.text.slice(0, 60)}`);
    try {
      if (t.href && t.href.startsWith('http')) {
        await page.goto(t.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
      } else {
        const el = page.locator(`text=${t.text.slice(0, 40)}`).first();
        await el.click({ timeout: 5000 });
      }
      await page.waitForTimeout(4000);
    } catch (e) {
      console.log('click failed: ' + e.message.slice(0, 100));
    }
  }

  console.log('\n=== API CALLS CAPTURED ===');
  for (const c of apiCalls) {
    console.log(`[${c.status}] ${c.url}`);
    if (c.preview) console.log(`    body: ${c.preview.slice(0, 200)}`);
  }
  if (apiCalls.length === 0) console.log('(none)');

  await page.screenshot({ path: 'tools/seb_probe_final.png', fullPage: false });
  await browser.close();
})().catch((e) => {
  console.error('FATAL: ' + e.message);
  process.exit(1);
});
