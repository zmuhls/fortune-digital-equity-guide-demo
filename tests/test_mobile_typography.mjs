import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { after, before, test } from 'node:test';
import { chromium, firefox, webkit, devices } from 'playwright';

// Render real, reviewed Wix text and layout, not a simplified typography mock.
// Default to the browser already used by CI; run all three locally with
// FORTUNE_TYPOGRAPHY_BROWSERS=firefox,chromium,webkit node --test this-file.
const root = new URL('../', import.meta.url);
const engines = { chromium, firefox, webkit };
const names = (process.env.FORTUNE_TYPOGRAPHY_BROWSERS || 'firefox').split(',');
const browsers = new Map();
let server, origin, desktop, mobile;

function buildHomeCaptures() {
  const result = spawnSync(process.env.PYTHON || 'python3', ['-c', `
import gzip, json
from pathlib import Path
from scripts import build_pages
root = Path.cwd()
rendered = {}
for mobile in (False, True):
    directory = root / 'replica-mobile' if mobile else root
    manifest = json.loads((directory / 'replica-manifest.json').read_text())
    home = next(page for page in manifest['pages'] if page['path'] == '/')
    route = {'path': '/', 'sourceUrl': home['url'], 'pageId': home['id'], 'page': {}}
    source = gzip.decompress((directory / home['file']).read_bytes()).decode()
    rendered['mobile' if mobile else 'desktop'] = build_pages.render_visual_snapshot_page(
        route, '', [route], source, mobile_layout=mobile, has_mobile_variant=not mobile)
print(json.dumps(rendered))
`], { cwd: fileURLToPath(root), encoding: 'utf8', maxBuffer: 12 * 1024 * 1024 });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  return JSON.parse(result.stdout);
}

before(async () => {
  ({ desktop, mobile } = buildHomeCaptures());
  server = createServer(async (request, response) => {
    const path = new URL(request.url, 'http://fixture.test').pathname;
    if (path === '/' || path === '/replica-mobile.html') {
      response.setHeader('Content-Type', 'text/html');
      response.end(path === '/' ? desktop : mobile);
      return;
    }
    if (!/^\/[a-zA-Z0-9_.-]+$/.test(path)) { response.writeHead(404); response.end(); return; }
    try {
      const bytes = await readFile(new URL(path.slice(1), root));
      response.setHeader('Content-Type', path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'application/json');
      response.end(bytes);
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  for (const name of names) {
    assert.ok(engines[name], `Unknown browser ${name}`);
    browsers.set(name, await engines[name].launch({ headless: true }));
  }
});
after(async () => {
  await Promise.all([...browsers.values()].map(browser => browser.close()));
  await new Promise(resolve => server?.close(resolve));
});

for (const name of names) for (const width of [375, 390, 430, 768, 1440]) {
  test(`${name}: source typography has separated lines and complete labels at ${width}px`, async () => {
    const context = await browsers.get(name).newContext({
      ...(width < 768 && name !== 'firefox' ? devices['iPhone 13'] : {}),
      viewport: { width, height: 900 },
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(`${origin}/?guide=0`);
    await page.waitForFunction(() => document.documentElement.dataset.replicaReady === 'true');
    const metrics = await page.evaluate(() => {
      const targets = /WELCOME|Digital tools|The Digital Equity Program|The program primarily|Choose A Service|Explore Learning Paths/;
      const blocks = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p')]
        .filter(node => targets.test(node.textContent.replace(/\s+/g, ' ')) && node.getBoundingClientRect().height)
        .map(node => {
          const range = document.createRange();
          range.selectNodeContents(node);
          const rows = new Map();
          for (const box of range.getClientRects()) {
            if (box.width < 1 || box.height < 1) continue;
            const key = Math.round(box.top);
            const row = rows.get(key) || { top: box.top, bottom: box.bottom };
            row.top = Math.min(row.top, box.top);
            row.bottom = Math.max(row.bottom, box.bottom);
            rows.set(key, row);
          }
          return { text: node.textContent.trim(), size: parseFloat(getComputedStyle(node).fontSize), rows: [...rows.values()].sort((a,b) => a.top - b.top) };
        });
      return {
        layout: document.documentElement.dataset.replicaLayout,
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        viewport: document.querySelector('meta[name="viewport"]').content,
        blocks,
      };
    });
    assert.equal(metrics.layout, width < 768 ? 'mobile' : 'desktop');
    assert.equal(metrics.overflow, false, 'Page must not extend beyond the viewport');
    assert.doesNotMatch(metrics.viewport, /user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:[,\s]|$)/i, 'User zoom remains available');
    assert.ok(metrics.blocks.length >= 5, 'Measure actual hero, introductory text, and section headings');
    const notice = await page.locator('#fortune-pilot-notice').boundingBox();
    const sourceHeader = await page.locator(width < 768 || width >= 980 ? '#SITE_HEADER' : '#fortune-responsive-header').boundingBox();
    assert.equal(notice.y, 0, 'Demo disclosure is at the top');
    assert.ok(sourceHeader.y >= notice.y + notice.height - 1, 'Disclosure must not overlap the source header');
    for (const block of metrics.blocks) {
      for (let index = 1; index < block.rows.length; index++) {
        assert.ok(block.rows[index].top >= block.rows[index - 1].bottom - 1,
          `Overlapping text lines: ${block.text}`);
      }
    }
    if (width < 768) {
      assert.equal(metrics.blocks.find(block => block.text.includes('WELCOME')).size, 30, 'Use the source phone heading, not the 56px desktop heading');
      for (const label of ['FIND A WORKSHOP', 'GET A DEVICE', 'GET SUPPORT', 'VIEW CALENDAR', 'INTERNSHIP', 'EXPLORE TOOLS']) {
        const link = page.getByRole('link', { name: label, exact: true });
        const complete = await link.evaluate(node => {
          const label = [...node.querySelectorAll('span')].find(child => child.textContent.trim() === node.textContent.trim()) || node;
          const range = document.createRange();
          range.selectNodeContents(label);
          const text = range.getBoundingClientRect();
          const box = label.getBoundingClientRect();
          return text.left >= box.left - 1 && text.right <= box.right + 1;
        });
        assert.equal(complete, true, `${label} must not be ellipsized`);
      }
      await page.locator('[data-replica-mobile-toggle]').click();
      await page.locator('[data-replica-mobile-close]').click({ timeout: 2000 });
      assert.equal(await page.locator('[data-replica-mobile-menu]').isVisible(), false,
        'The disclosure must not intercept the mobile menu close button');
    }
    assert.deepEqual(errors, []);
    await context.close();
  });
}
