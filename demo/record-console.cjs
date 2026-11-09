'use strict';
// Records the ReserveGuard ops-console walkthrough as demo/reserveguard-console.webm (1280x720).
// Run against a local fake-Airwallex API so the recording never moves sandbox money:
//   env -u AIRWALLEX_API_KEY -u AIRWALLEX_CLIENT_ID -u AIRWALLEX_BASE_URL USE_FAKE_AIRWALLEX=true \
//     USE_LLM=false RESERVEGUARD_API_TOKEN=demo-token RESERVEGUARD_DB_PATH=/tmp/rg-demo.db \
//     uv run uvicorn reserveguard.api:app --port 8765
//   node demo/record-console.cjs --rehearse   # checks every selector, records nothing
//   node demo/record-console.cjs              # records
// Env: BASE_URL (default http://localhost:8765), DEMO_TOKEN (default demo-token),
//      PW (path to a playwright/playwright-core package), CHROME (browser executable).
// Start from an empty ledger: the story assumes no settlement has run yet.
const { chromium } = require(process.env.PW || 'playwright');
const path = require('path');
const fs = require('fs');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8765';
const TOKEN = process.env.DEMO_TOKEN || 'demo-token';
const OUT = path.join(__dirname, 'reserveguard-console.webm');
const REHEARSAL = process.argv.includes('--rehearse');
const SIZE = { width: 1280, height: 720 };

async function injectOverlays(page) {
  await page.evaluate(() => {
    if (document.getElementById('demo-cursor')) return;
    const c = document.createElement('div');
    c.id = 'demo-cursor';
    c.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M5 3L19 12L12 13L9 20L5 3Z" fill="white" stroke="black" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    c.style.cssText = 'position:fixed;z-index:999999;pointer-events:none;width:24px;height:24px;left:0;top:0;transition:left .1s,top .1s;filter:drop-shadow(1px 1px 2px rgba(0,0,0,.3))';
    document.body.appendChild(c);
    document.addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; });
    const bar = document.createElement('div');
    bar.id = 'demo-subtitle';
    bar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:999998;text-align:center;padding:12px 24px;background:rgba(0,0,0,.75);color:#fff;font:500 16px -apple-system,"Segoe UI",sans-serif;opacity:0;transition:opacity .3s;pointer-events:none';
    document.body.appendChild(bar);
  });
}

async function say(page, text) {
  await page.evaluate((t) => {
    const bar = document.getElementById('demo-subtitle');
    bar.textContent = t;
    bar.style.opacity = t ? '1' : '0';
  }, text);
  if (text) await page.waitForTimeout(1200);
}

async function moveAndClick(page, selector, label, after = 1500) {
  const el = page.locator(selector).first();
  if (!(await el.isVisible())) throw new Error(`"${label}" not visible: ${selector}`);
  await el.scrollIntoViewIfNeeded();
  const box = await el.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 });
  await page.waitForTimeout(400);
  await el.click();
  await page.waitForTimeout(after);
}

async function typeSlowly(page, selector, text, label) {
  await moveAndClick(page, selector, label, 300);
  await page.locator(selector).fill('');
  await page.locator(selector).pressSequentially(text, { delay: 40 });
  await page.waitForTimeout(500);
}

async function scrollTo(page, selector) {
  await page.locator(selector).first().evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  await page.waitForTimeout(1800);
}

async function rehearse(page) {
  const checks = [
    ['#token-input', 'token field'], ['button[type=submit]', 'Connect'],
  ];
  for (const [sel, label] of checks) if (!(await page.locator(sel).first().isVisible())) throw new Error(`REHEARSAL FAIL: ${label}`);
  await page.fill('#token-input', TOKEN);
  await page.click('button[type=submit]');
  await page.waitForTimeout(1200);
  for (const [sel, label] of [['#settle-open', 'Run settlement'], ['button:has-text("Seller C")', 'Seller C'],
    ['#load-risk', 'Sample: complaints'], ['#assess', 'Assess'], ['#ledger-heading', 'Ledger'], ['#refresh-checks', 'Re-run checks']]) {
    if (!(await page.locator(sel).first().isVisible())) throw new Error(`REHEARSAL FAIL: ${label} (${sel})`);
    console.log(`REHEARSAL OK: ${label}`);
  }
  console.log('REHEARSAL PASSED');
}

async function record(page) {
  await injectOverlays(page);
  await say(page, 'ReserveGuard ops console');
  await typeSlowly(page, '#token-input', TOKEN, 'API token');
  await moveAndClick(page, 'button[type=submit]', 'Connect', 2500);
  await injectOverlays(page);

  await say(page, 'Step 1 - Run a settlement cycle');
  await moveAndClick(page, '#settle-open', 'Run settlement cycle', 1500);
  await say(page, 'Gross sales per seller; reserves are held by tier');
  await moveAndClick(page, 'dialog[open] button[type=submit]', 'Pay out', 3000);

  await say(page, 'Step 2 - Seller C: new tier, 15% held');
  await moveAndClick(page, 'button:has-text("Seller C")', 'Seller C', 2500);

  await say(page, 'Step 3 - New risk evidence arrives');
  await scrollTo(page, '#evidence-heading');
  await moveAndClick(page, '#load-risk', 'Sample: complaints', 1500);
  await say(page, 'Claude reads the evidence; fixed rules set the weights');
  await moveAndClick(page, '#assess', 'Assess seller', 3000);

  await say(page, 'Step 4 - Why this rate: every signal cites its evidence');
  await scrollTo(page, '#why-heading');
  await page.waitForTimeout(2500);

  await say(page, 'Step 5 - Above the threshold, so ops approves');
  await moveAndClick(page, '#approve', 'Approve rate change', 2000);
  await say(page, 'Two clicks: approve, then confirm the charge');
  await moveAndClick(page, '#approve', 'Confirm charge', 3000);

  await say(page, 'Step 6 - Ledger and reconciliation');
  await scrollTo(page, '#ledger-heading');
  await page.waitForTimeout(2000);
  await moveAndClick(page, '#refresh-checks', 'Re-run checks', 3000);
  await say(page, 'Every check passes: no seller funds mixed');
  await page.waitForTimeout(2500);
  await say(page, '');
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME });
  const context = await browser.newContext(REHEARSAL ? { viewport: SIZE } : { viewport: SIZE, recordVideo: { dir: path.join(__dirname, '.video-tmp'), size: SIZE } });
  const page = await context.newPage();
  let failed = false;
  try {
    await page.goto(`${BASE_URL}/console`, { waitUntil: 'networkidle' });
    await (REHEARSAL ? rehearse(page) : record(page));
  } catch (err) {
    failed = true;
    console.error('DEMO ERROR:', err.message);
  } finally {
    await context.close();
    if (!REHEARSAL) {
      const src = await page.video().path();
      fs.copyFileSync(src, OUT);
      fs.rmSync(path.dirname(src), { recursive: true, force: true });
      console.log(failed ? 'Partial video saved:' : 'Video saved:', OUT);
    }
    await browser.close();
    process.exitCode = failed ? 1 : 0;
  }
})();
