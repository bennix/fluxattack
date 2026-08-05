'use strict';

/**
 * Authorized Playwright instrumentation / automation probe against FLUX v5.
 * Measures: webdriver flags, secret exposure, canvas paint, in-page attack button.
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('/Users/nellertcai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const BASE = process.env.FLUX_URL || 'http://127.0.0.1:5056';
const OUT = path.join(__dirname, '..', 'output');
fs.mkdirSync(OUT, { recursive: true });

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function probe(page) {
  return page.evaluate(() => {
    const canvas = document.getElementById('cv');
    const ctx = canvas?.getContext('2d');
    let nonblank = 0;
    if (ctx && canvas) {
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] || pixels[i + 1] || pixels[i + 2] || pixels[i + 3]) nonblank++;
      }
    }
    return {
      webdriver: navigator.webdriver,
      userAgent: navigator.userAgent.slice(0, 120),
      languages: navigator.languages,
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemory: navigator.deviceMemory || null,
      pluginsLength: navigator.plugins?.length ?? null,
      chromeRuntime: typeof window.chrome,
      status: document.getElementById('status')?.textContent || '',
      riskTxt: document.getElementById('riskTxt')?.textContent || '',
      exposures: {
        P: typeof window.P,
        token: typeof window.token,
        Server: typeof window.Server,
        gP: typeof window.gP,
        glyphCh: typeof window.glyphCh,
        viscapDebug: typeof window.__viscapDebug,
        challengeAnswer: typeof window.challengeAnswer,
      },
      nonblankPixels: nonblank,
      hasAttackButton: !!document.getElementById('attack'),
      hasCheckButton: !!document.getElementById('check'),
    };
  });
}

async function runHeadless() {
  const browser = await chromium.launch({
    headless: true,
    channel: 'chrome',
  });
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => {
    const t = document.querySelector('#status')?.textContent || '';
    return t.includes('拖动') || t.includes('失败') || t.includes('领取');
  }, null, { timeout: 15000 }).catch(() => {});
  await sleep(800);
  const before = await probe(page);

  // Click built-in boundary-matching attack (defensive demo control on page)
  let afterAttack = null;
  let verifyResult = null;
  if (before.hasAttackButton) {
    await page.click('#attack');
    await sleep(1200);
    afterAttack = {
      status: await page.textContent('#status'),
      riskTxt: await page.textContent('#riskTxt'),
    };
    await page.click('#check');
    await sleep(1500);
    verifyResult = {
      status: await page.textContent('#status'),
      riskTxt: await page.textContent('#riskTxt'),
      verifyVisible: await page.evaluate(() => document.getElementById('verify')?.classList.contains('show')),
      verifyFail: await page.evaluate(() => document.getElementById('verify')?.classList.contains('fail')),
    };
  }

  // Naive robotic pointer drags (does not know answer)
  await page.click('#reset').catch(() => {});
  await sleep(1000);
  const box = await page.locator('#cv').boundingBox();
  if (box) {
    for (let i = 0; i < 4; i++) {
      const x0 = box.x + 40 + i * 30;
      const y0 = box.y + 40 + i * 20;
      await page.mouse.move(x0, y0);
      await page.mouse.down();
      for (let s = 1; s <= 8; s++) {
        await page.mouse.move(x0 + s * 12, y0 + s * 10);
        await sleep(20);
      }
      await page.mouse.up();
      await sleep(50);
    }
    await page.click('#check');
    await sleep(1200);
  }
  const afterRobot = {
    status: await page.textContent('#status'),
    riskTxt: await page.textContent('#riskTxt'),
    verifyFail: await page.evaluate(() => document.getElementById('verify')?.classList.contains('fail')),
  };

  await browser.close();
  return { mode: 'headless_chromium', before, afterAttack, verifyResult, afterRobot };
}

async function runStealthish() {
  // Light anti-automation args only; not a full CloakBrowser stack.
  const browser = await chromium.launch({
    headless: true,
    channel: 'chrome',
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const context = await browser.newContext({
    viewport: { width: 1100, height: 800 },
    userAgent:
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });
  const page = await context.newPage();
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await sleep(1000);
  const info = await probe(page);
  await browser.close();
  return { mode: 'headed_stealthish', info };
}

(async () => {
  const results = { target: BASE, timestamp: new Date().toISOString() };
  results.headless = await runHeadless();
  try {
    results.stealthish = await runStealthish();
  } catch (err) {
    results.stealthish = { error: String(err) };
  }
  const secretsHidden = Object.values(results.headless.before.exposures).every(v => v === 'undefined');
  results.summary = {
    secrets_not_exposed: secretsHidden,
    canvas_painted: results.headless.before.nonblankPixels > 5000,
    headless_webdriver_true: results.headless.before.webdriver === true,
    stealthish_webdriver: results.stealthish?.info?.webdriver ?? null,
    inpage_attack_did_not_auto_pass:
      !String(results.headless.verifyResult?.status || '').includes('验证通过'),
    robotic_drag_did_not_auto_pass:
      !String(results.headless.afterRobot?.status || '').includes('验证通过'),
  };
  const out = path.join(OUT, 'browser_probe_results.json');
  fs.writeFileSync(out, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results.summary, null, 2));
  console.log('WROTE', out);
})().catch(err => {
  console.error(err);
  process.exit(1);
});
