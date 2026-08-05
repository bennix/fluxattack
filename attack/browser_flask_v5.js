'use strict';

const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('/Users/nellertcai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || '/Users/nellertcai/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3';
const deps = process.env.VISCAP_FLASK_DEPS || '/tmp/viscap_flask_deps';
const port = process.env.PORT || '5055';
const env = {
  ...process.env,
  PORT: port,
  PYTHONPATH: `${deps}:${root}:${process.env.PYTHONPATH || ''}`,
};

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForServer() {
  const url = `http://127.0.0.1:${port}/api/health`;
  for (let i = 0; i < 80; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch (_) {
      // Server is still starting.
    }
    await wait(150);
  }
  throw new Error('Flask server did not become ready');
}

(async () => {
  const server = spawn(python, ['-m', 'flask_v5.app'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  server.stderr.on('data', chunk => { stderr += chunk.toString(); });
  try {
    await waitForServer();
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
    await page.goto(`http://127.0.0.1:${port}/`);
    await page.waitForFunction(() => document.querySelector('#status')?.textContent.includes('拖动碎片'), null, { timeout: 10000 });
    await page.waitForTimeout(500);
    const result = await page.evaluate(() => {
      const canvas = document.getElementById('cv');
      const ctx = canvas.getContext('2d');
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let nonblank = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] || pixels[i + 1] || pixels[i + 2]) nonblank++;
      }
      return {
        exposures: {
          P: typeof P,
          token: typeof token,
          Server: typeof Server,
          gP: typeof gP,
          glyphCh: typeof glyphCh,
          viscapDebug: typeof window.__viscapDebug,
        },
        status: document.getElementById('status').textContent,
        nonblankPixels: nonblank,
      };
    });
    await browser.close();
    const ok = Object.values(result.exposures).every(v => v === 'undefined') && result.nonblankPixels > 5000;
    console.log(JSON.stringify({ ...result, ok }, null, 2));
    process.exitCode = ok ? 0 : 1;
  } catch (err) {
    console.error(JSON.stringify({ ok: false, error: String(err), stderr }, null, 2));
    process.exitCode = 1;
  } finally {
    server.kill();
  }
})();

