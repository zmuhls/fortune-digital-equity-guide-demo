import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { after, before, test } from "node:test";
import { firefox } from "playwright";
import { sanitizeDocument } from "../scripts/capture_replica.mjs";

const root = new URL("../", import.meta.url);
let browser, server, origin;
const requests = [];

function fixture(mobile = false) {
  const branch = (label, attribute) => `<details ${attribute}><summary>${label}</summary><ul>${[1, 2, 3, 4, 5, 6].map(i => `<li style="height:44px"><a href="#target">${label} item ${i}</a></li>`).join("")}</ul></details>`;
  return `<html data-fortune-visual-mirror="true" data-replica-layout="${mobile ? "mobile" : "desktop"}" style="--site-width:${mobile ? 320 : 980}px"><head><meta charset="utf-8"><meta name="fortune-replica-source" content="https://www.fortunedigitalequity.org/"><link rel="stylesheet" href="/replica-widget.css"></head><body style="margin:0">
    <div id="SITE_CONTAINER" style="width:${mobile ? 320 : 980}px"><header id="SITE_HEADER">
    ${mobile ? `<button data-replica-mobile-toggle><span style="visibility:visible">Menu</span></button><nav id="menu" data-replica-mobile-menu style="position:fixed;top:0;right:0;width:260px;height:100vh;background:white"><div style="height:1000px">${branch("Services", "data-replica-mobile-submenu")}${branch("Resources", "data-replica-mobile-submenu")}</div></nav>` : `<nav>${branch("Services", "data-replica-static-menu")}${branch("Resources", "data-replica-static-menu")}</nav>`}
    </header>${mobile ? '<section id="fixture-hero" style="position:relative;width:320px;height:200px"><div id="bgLayers_fixture" style="position:absolute;inset:0"><div id="bgMedia_fixture" style="height:1000px;margin-top:-500px"><wow-image style="position:sticky;top:0;height:1000px"><img alt="Source background fixture" style="width:320px;height:532px;object-fit:cover;object-position:50% 0"></wow-image></div></div></section>' : ""}<a id="jump" href="${mobile ? "/mobile" : ""}/index.html#target">Jump</a>
    <section style="height:1300px">Page content</section><section id="target" style="height:1500px">Target section</section>
    </div><script src="/replica-shell.js?v=test" data-source-url="https://www.fortunedigitalequity.org/" ${mobile ? "" : 'data-mobile-src="/native-mobile.html"'}></script></body></html>`;
}

function calendarFixture() {
  const day = (label, date, title) => `<li data-hook="daily-agenda-day"><section id="${date}"><div role="presentation"><span>${label}</span></div><ul><li data-hook="daily-agenda-slot"><div class="s__1sSNBn">${title}</div><span class="ssaqcAw">2:00 pm</span><span class="sf9j229">Main Office (LIC)</span><span class="sHwjQAH">Source instructor</span><span>15 spots left</span><a data-replica-live-action="true" href="https://www.fortunedigitalequity.org/calendar">REGISTER</a></li></ul></section></li>`;
  return fixture(true).replaceAll('data-source-url="https://www.fortunedigitalequity.org/"', 'data-source-url="https://www.fortunedigitalequity.org/calendar"')
    .replace('</head>', '<meta name="fortune-replica-captured-at" content="2026-09-24T04:00:00Z"></head>')
    .replace('<section style="height:1300px">Page content</section>', `<main><div data-hook="DailyAgenda-wrapper"><div data-hook="weekly-date-picker"><span data-hook="weekly-date-picker-caption">November 2026</span></div><div data-hook="filters-root"></div><ul data-hook="daily-agenda-content">${day("Wednesday, September23", "September-23", "Past event")}${day("Thursday, September 24", "September-24", "Current event")}</ul></div></main>`);
}

function newsFixture() {
  return fixture().replaceAll('data-source-url="https://www.fortunedigitalequity.org/"', 'data-source-url="https://www.fortunedigitalequity.org/news"')
    .replace('data-mobile-src="/native-mobile.html"', '')
    .replace('<section style="height:1300px">Page content</section>', '<main><div data-hook="search-input"><div class="kD1Yyw search-input"><div aria-label="Search"><svg width="19" height="19"><path d="M0 0h19v19z" /></svg></div></div></div></main>');
}

function mobileNewsFixture() {
  return fixture(true).replaceAll('data-source-url="https://www.fortunedigitalequity.org/"', 'data-source-url="https://www.fortunedigitalequity.org/news"')
    .replace('<section style="height:1300px">Page content</section>', '<main><a aria-label="Search" href="https://www.fortunedigitalequity.org/news/search">Search</a><p data-replica-static-control-label="true">Select blog category</p></main>');
}

function menuLabelsFixture() {
  return fixture(true)
    .replaceAll(/<summary>(Services|Resources)<\/summary>/g, '<summary class="keDKhi"><span data-testid="linkWrapper"><div data-testid="linkElement">$1</div></span><span aria-hidden="true"><span>⌄</span></span></summary>')
    .replace('</head>', '<style>.keDKhi{display:grid;grid-template-columns:1fr;height:56px;position:relative}.keDKhi>[data-testid=linkWrapper]{position:relative}.keDKhi [data-testid=linkElement]{position:absolute;inset:0;overflow:hidden;line-height:56px}</style></head>');
}

function tabletBackgroundFixture() {
  return fixture().replace('data-mobile-src="/native-mobile.html"', '')
    .replace('<section style="height:1300px">Page content</section>', '<section id="fixture-hero" style="position:relative;width:980px;height:200px"><div id="bgLayers_fixture" style="position:absolute;inset:0"><div id="bgMedia_fixture" style="height:1000px;margin-top:-500px"><wow-image style="position:sticky;top:0;height:1000px"><img alt="Source background" style="width:980px;height:532px;object-fit:cover;object-position:50% 0"></wow-image></div></div></section>');
}

before(async () => {
  server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://fixture.test");
    requests.push({ method: request.method, path: url.pathname });
    const mobile = url.pathname.startsWith("/mobile");
    if (url.pathname === "/hung-mobile.html" || url.pathname === "/hung-service.js") {
      response.writeHead(200, { "Content-Type": url.pathname.endsWith('.js') ? "text/javascript" : "text/html" });
      response.write(" "); // Headers arrive, but the optional body never finishes.
      return;
    }
    if (url.pathname === "/startup-fixture/") {
      response.setHeader("Content-Type", "text/html");
      response.end(fixture().replace('data-mobile-src="/native-mobile.html"', 'data-mobile-src="/hung-mobile.html"')
        .replace('<header id="SITE_HEADER">', '<header id="SITE_HEADER"><a aria-label="Homepage" href="/">Source brand</a>'));
      return;
    }
    if (url.pathname === "/calendar-fixture/") { response.setHeader("Content-Type", "text/html"); response.end(calendarFixture()); return; }
    if (url.pathname === "/news/") { response.setHeader("Content-Type", "text/html"); response.end(newsFixture()); return; }
    if (url.pathname === "/mobile-news/") { response.setHeader("Content-Type", "text/html"); response.end(mobileNewsFixture()); return; }
    if (url.pathname === "/menu-labels/") { response.setHeader("Content-Type", "text/html"); response.end(menuLabelsFixture()); return; }
    if (url.pathname === "/tablet-background/") { response.setHeader("Content-Type", "text/html"); response.end(tabletBackgroundFixture()); return; }
    if (url.pathname === "/native-mobile.html") { response.setHeader("Content-Type", "text/html"); response.end(fixture(true)); return; }
    if (["/", "/index.html", "/mobile/", "/mobile/index.html"].includes(url.pathname)) {
      response.setHeader("Content-Type", "text/html"); response.end(fixture(mobile)); return;
    }
    if (url.pathname.startsWith("/api/")) {
      response.setHeader("Content-Type", "application/json"); response.end('{"ok":true}'); return;
    }
    const file = url.pathname === "/sidecar.html" ? "index.html" : url.pathname.slice(1);
    if (!/^[a-zA-Z0-9_.-]+$/.test(file)) { response.writeHead(404); response.end(); return; }
    try {
      const bytes = await readFile(new URL(file, root));
      response.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".json") ? "application/json" : "text/html");
      response.end(bytes);
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await firefox.launch({ headless: true });
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });

async function pageFor(width) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  await page.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  return page;
}

test("news Search expands accessibly and sends encoded queries only to official search", async () => {
  const page = await pageFor(980);
  await page.goto(`${origin}/news/?guide=0`);
  const toggle = page.getByRole('button', { name: 'Search', exact: true });
  const input = page.getByRole('searchbox', { name: 'Search news on Fortune’s site' });
  assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
  await toggle.click();
  assert.equal(await input.isVisible(), true, await page.locator('.search-input').evaluate(node => node.outerHTML));
  assert.equal(await input.evaluate(node => node === document.activeElement), true);
  await input.press('Escape');
  assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
  await toggle.press('Enter');
  await input.press('Enter');
  assert.equal(page.url(), `${origin}/news/?guide=0`);
  const query = 'email / café?';
  await input.fill(query);
  const destination = `https://www.fortunedigitalequity.org/news/search/${encodeURIComponent(query)}`;
  const navigation = page.waitForRequest(destination);
  await input.press('Enter');
  assert.equal((await navigation).url(), destination);
  await page.close();
});

test("native news retains the real search handoff and restores only verified category routes", async () => {
  const page = await pageFor(375);
  await page.goto(`${origin}/mobile-news/?guide=0`);
  assert.equal(await page.getByRole('link', { name: 'Search' }).getAttribute('href'), 'https://www.fortunedigitalequity.org/news/search');
  const select = page.getByRole('combobox', { name: 'Select blog category' });
  assert.deepEqual(await select.locator('option').allTextContents(), ['All Posts', 'General', 'Press', 'Updates', 'Classes', 'Events', 'Student', 'Fortune Bloggers']);
  const destination = `${origin}/news/categories/fortune-bloggers/`;
  const navigation = page.waitForRequest(destination);
  await select.selectOption({ label: 'Fortune Bloggers' });
  assert.equal((await navigation).url(), destination);
  await page.close();
});

test("desktop navigation closes the previous sibling without affecting FAQ disclosures", async () => {
  const page = await pageFor(1440);
  await page.goto(`${origin}/?guide=0`);
  await page.locator('details[data-replica-static-menu] > summary').nth(0).click();
  await page.locator('details[data-replica-static-menu] > summary').nth(1).click();
  assert.equal(await page.locator('details[data-replica-static-menu][open]').count(), 1);
  assert.equal(await page.locator('html').getAttribute('data-replica-ready'), 'true');
  assert.match(await page.locator('details[data-replica-static-menu][open] summary').innerText(), /Resources/i);
  await page.close();
});

test("a stalled native layout body falls back without blocking navigation or the guide", async () => {
  const page = await pageFor(375);
  const started = Date.now();
  await page.goto(`${origin}/startup-fixture/`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.documentElement.dataset.replicaReady === 'true', { timeout: 5000 });
  assert.ok(Date.now() - started < 5000);
  assert.equal(await page.locator('html').getAttribute('data-replica-layout'), 'desktop');
  await page.getByRole('button', { name: 'Open navigation menu' }).click();
  assert.equal(await page.locator('#fortune-responsive-navigation').getAttribute('data-open'), 'true');
  await page.locator('#fortune-sidecar-launcher').waitFor({ state: 'visible' });
  await page.locator('#fortune-sidecar-launcher').click();
  await page.frameLocator('#fortune-sidecar-frame').locator('#guide-panel').waitFor({ state: 'visible' });
  assert.equal(requests.filter(r => r.path === '/api/chat').length, 0);
  await page.close();
});

test("a stalled optional service import cannot block guide creation or replica readiness", async () => {
  const page = await pageFor(980);
  await page.route(`${origin}/service-page/startup/`, route => route.fulfill({
    contentType: 'text/html',
    body: fixture().replace('data-mobile-src="/native-mobile.html"', '')
      .replaceAll('data-source-url="https://www.fortunedigitalequity.org/"', 'data-source-url="https://www.fortunedigitalequity.org/service-page/startup"'),
  }));
  await page.route(`${origin}/replica-services.js*`, route => route.continue({ url: `${origin}/hung-service.js` }));
  const started = Date.now();
  await page.goto(`${origin}/service-page/startup/`, { waitUntil: 'domcontentloaded' });
  await page.locator('#fortune-sidecar-frame').waitFor({ state: 'attached', timeout: 1000 });
  await page.waitForFunction(() => document.documentElement.dataset.replicaReady === 'true', { timeout: 5000 });
  assert.ok(Date.now() - started < 5000);
  await page.locator('#fortune-sidecar-launcher').waitFor({ state: 'visible' });
  await page.locator('#fortune-sidecar-launcher').click();
  await page.frameLocator('#fortune-sidecar-frame').locator('#guide-panel').waitFor({ state: 'visible' });
  assert.equal(requests.filter(r => r.path === '/api/chat').length, 0);
  await page.close();
});

test("large phone native menu scrolls within the viewport and same-page hash jumps do not reload", async () => {
  const page = await pageFor(767);
  await page.goto(`${origin}/mobile/?guide=0`);
  await page.locator('[data-replica-mobile-toggle]').click();
  const menu = page.locator('[data-replica-mobile-menu]');
  assert.ok((await menu.boundingBox()).height <= 1001);
  await page.getByText("Services", { exact: true }).click();
  await page.getByText("Resources", { exact: true }).click();
  await page.getByText("Resources item 6", { exact: true }).scrollIntoViewIfNeeded();
  const bottom = await page.getByText("Resources item 6", { exact: true }).boundingBox();
  assert.ok(bottom.y >= 0 && bottom.y + bottom.height <= 1001);
  await page.keyboard.press("Escape");
  assert.equal(await menu.isVisible(), false);
  assert.equal(await page.locator('#jump').getAttribute('href'), '#target');
  const documentsBefore = requests.filter(r => r.path === "/mobile/" || r.path === "/mobile/index.html").length;
  await page.locator('#jump').click();
  await page.waitForTimeout(100);
  assert.equal(requests.filter(r => r.path === "/mobile/" || r.path === "/mobile/index.html").length, documentsBefore);
  const targetTop = (await page.locator('#target').boundingBox()).y;
  assert.ok(targetTop >= 0 && targetTop < 300, `target top ${targetTop} should be visible after its CSS scroll margin`);
  await page.close();
});

test("native phone layout ends at 767px and tablet uses the fitted desktop capture", async () => {
  for (const width of [767, 768, 979, 980]) {
    const page = await pageFor(width);
    await page.goto(`${origin}/?guide=0`);
    await page.waitForFunction(expected => document.documentElement.dataset.replicaLayout === expected, width <= 767 ? "mobile" : "desktop");
    assert.equal(await page.locator('html').getAttribute('data-replica-layout'), width <= 767 ? "mobile" : "desktop");
    const zoom = await page.locator('#SITE_CONTAINER').evaluate(e => Number.parseFloat(getComputedStyle(e).zoom));
    assert.ok(Math.abs(zoom - (width <= 767 ? width / 320 : Math.min(1, width / 980))) < .001);
    await page.close();
  }
});

test("tablet fragment links scroll body when it, not window, is the captured page scroller", async () => {
  const page = await pageFor(768);
  await page.goto(`${origin}/?guide=0`);
  await page.addStyleTag({ content: "html{height:100%;overflow:hidden}body{height:100%;overflow:auto}" });
  await page.locator('#jump').click();
  await page.waitForTimeout(100);
  const metrics = await page.evaluate(() => ({ windowY: scrollY, bodyY: document.body.scrollTop, top: document.querySelector('#target').getBoundingClientRect().top }));
  assert.equal(metrics.windowY, 0);
  assert.ok(metrics.bodyY > 0);
  assert.ok(metrics.top >= 0 && metrics.top < 300);
  await page.close();
});

test("native captured parallax image paints its own section rather than an obsolete sticky offset", async () => {
  const page = await pageFor(375);
  await page.goto(`${origin}/mobile/?guide=0`);
  const section = await page.locator('#fixture-hero').boundingBox();
  const image = await page.locator('#fixture-hero img').boundingBox();
  assert.ok(Math.abs(section.y - image.y) < 1);
  assert.ok(Math.abs(section.height - image.height) < 1);
  assert.equal(await page.locator('[data-replica-static-background]').count(), 1);
  await page.close();
});

test("native menu disclosure labels and arrows share one unclipped source-height row", async () => {
  const page = await pageFor(375);
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto(`${origin}/menu-labels/?guide=0`);
  await page.locator('[data-replica-mobile-toggle]').click();
  for (const summary of await page.locator('[data-replica-mobile-submenu] > summary').all()) {
    const row = await summary.boundingBox();
    const label = await summary.locator('[data-testid="linkElement"]').boundingBox();
    const arrow = await summary.locator('[aria-hidden]').boundingBox();
    assert.ok(Math.abs(row.height - label.height) < 1);
    assert.ok(Math.abs(row.y - label.y) < 1);
    assert.ok(Math.abs(row.y - arrow.y) < 1);
    assert.ok(arrow.x > label.x);
  }
  const close = page.locator('[data-replica-mobile-close]');
  assert.equal(await page.locator('[data-replica-mobile-toggle]').isVisible(), false);
  assert.equal(await page.locator('[data-replica-mobile-toggle] span').isVisible(), false);
  const closeBox = await close.boundingBox();
  assert.ok(closeBox.width >= 44 && closeBox.height >= 44);
  await close.click();
  assert.equal(await page.locator('[data-replica-mobile-menu]').isVisible(), false);
  assert.equal(await page.locator('[data-replica-mobile-toggle]').getAttribute('aria-expanded'), 'false');
  assert.equal(await page.locator('[data-replica-mobile-toggle]').isVisible(), true);
  await page.close();
});

test("fitted desktop backgrounds fill the tablet hero without changing full desktop capture", async () => {
  const page = await pageFor(768);
  await page.goto(`${origin}/tablet-background/?guide=0`);
  const section = await page.locator('#fixture-hero').boundingBox();
  const image = await page.locator('#fixture-hero img').boundingBox();
  assert.ok(Math.abs(section.y - image.y) < 1);
  assert.ok(Math.abs(section.height - image.height) < 1);
  await page.setViewportSize({ width: 980, height: 1000 });
  await page.waitForFunction(() => !document.querySelector('[data-replica-static-background]'));
  assert.equal(await page.locator('wow-image').evaluate(node => getComputedStyle(node).position), 'sticky');
  await page.close();
});

test("capture restores only missing source-declared Wix image assets with safe image types", async () => {
  const page = await pageFor(375);
  const inputs = [
    { uri: "e58568_source~mv2.png", expected: "https://static.wixstatic.com/media/e58568_source~mv2.png" },
    { uri: "https://static.wixstatic.com/media/source.jpg", expected: "https://static.wixstatic.com/media/source.jpg" },
    { uri: "../other.jpg" }, { uri: "https://evil.example/photo.jpg" },
    { uri: "http://static.wixstatic.com/media/source.jpg" }, { uri: "javascript:alert(1)" },
    { uri: "e58568_source~mv2.png", mimeType: "text/html" }, { uri: "source.html" },
    { uri: "https://static.wixstatic.com/media/source.jpg?redirect=evil" },
    { uri: "e58568_source~mv2.png", existing: "https://example.test/keep.jpg", expected: "https://example.test/keep.jpg" },
  ];
  await page.setContent(`<html><head></head><body>${inputs.map((input, i) => `<wow-image data-image-info='${JSON.stringify({ imageData: { uri: input.uri, mimeType: input.mimeType } })}'><img id="image-${i}" ${input.existing ? `src="${input.existing}"` : ""}></wow-image>`).join("")}</body></html>`);
  await page.evaluate(`(${sanitizeDocument.toString()})()`);
  for (const [i, input] of inputs.entries()) assert.equal(await page.locator(`#image-${i}`).getAttribute('src'), input.expected || null);
  await page.close();
});

test("future captures keep original share and print SVGs instead of overflowing sentence labels", async () => {
  const page = await pageFor(375);
  await page.setContent('<html><body><button class="source-icon" aria-label="Share via Facebook"><svg width="19"><path d="M0 0h19v19z"/></svg></button><button aria-label="Print Post"><svg width="19"><path d="M2 2h15v15z"/></svg></button></body></html>');
  await page.evaluate(`(${sanitizeDocument.toString()})()`);
  assert.equal(await page.locator('a[data-replica-live-action] svg').count(), 2);
  assert.equal(await page.locator('a[data-replica-live-action]').first().textContent(), '');
  assert.equal(await page.locator('a[data-replica-live-action]').last().getAttribute('title'), 'Print Post on the Digital Equity site');
  assert.equal(await page.locator('button').count(), 0);
  await page.close();
});

test("sanitizing gallery expansion preserves every original nested photo and source destination", async () => {
  const page = await pageFor(375);
  await page.goto(`${origin}/?guide=0`);
  await page.waitForFunction(() => document.documentElement.dataset.replicaReady === 'true');
  await page.setContent('<html><body><section aria-label="Gallery"><button class="source-photo" aria-label="Expand image"><span><img src="https://static.wixstatic.com/media/source-photo.jpg" alt="Original workshop photo"></span></button><div aria-hidden="true"><button aria-label="Expand image"><picture><source srcset="https://static.wixstatic.com/media/source-photo-2.webp"><img src="https://static.wixstatic.com/media/source-photo-2.jpg" alt="Second original photo"></picture></button></div></section></body></html>');
  await page.evaluate(`(${sanitizeDocument.toString()})()`);
  assert.equal(await page.locator('section img').count(), 2, await page.locator('body').innerHTML());
  assert.equal(await page.locator('section picture source').getAttribute('srcset'), 'https://static.wixstatic.com/media/source-photo-2.webp');
  assert.equal(await page.locator('section img').first().getAttribute('src'), 'https://static.wixstatic.com/media/source-photo.jpg');
  assert.equal(await page.locator('a.source-photo').getAttribute('href'), `${origin}/?guide=0`);
  assert.equal(await page.locator('a.source-photo').getAttribute('target'), '_blank');
  assert.equal(await page.locator('button').count(), 0);
  await page.close();
});

test("native calendar weekday-first and compact labels restore current controls without stale capacities", async () => {
  const page = await pageFor(375);
  await page.clock.install({ time: new Date("2026-09-24T04:30:00Z") });
  await page.goto(`${origin}/calendar-fixture/?guide=0`);
  await page.locator('[data-replica-calendar-controls]').waitFor();
  assert.match(await page.locator('[data-replica-calendar-controls] strong').innerText(), /September 2026/);
  assert.doesNotMatch(await page.locator('main').innerText(), /15 spots left/);
  assert.equal(await page.locator('[data-hook="daily-agenda-day"]:visible a').innerText(), 'REGISTER');
  assert.equal(await page.locator('#September-23 [aria-disabled="true"]').innerText(), 'CLOSED');
  assert.equal(await page.locator('[data-hook="daily-agenda-day"]:visible').count(), 1);
  assert.match(await page.locator('[data-hook="daily-agenda-day"]:visible').innerText(), /Current event/);
  assert.equal(await page.locator('[data-hook="filters-root"] select').count(), 3);
  await page.getByRole('button', { name: "Next week", exact: true }).click();
  assert.equal(await page.locator('[data-hook="daily-agenda-day"]:visible').count(), 0);
  await page.close();
});

test("closed guide accepts input only on its launcher and opens/closes normally without chat submission", async () => {
  const page = await pageFor(1440);
  await page.goto(`${origin}/`);
  const launcher = page.locator('#fortune-sidecar-launcher');
  await launcher.waitFor({ state: "visible" });
  const state = await page.evaluate(() => {
    const frame = document.querySelector('#fortune-sidecar-frame');
    const host = frame.getBoundingClientRect();
    const proxy = document.querySelector('#fortune-sidecar-launcher').getBoundingClientRect();
    const real = frame.contentDocument.querySelector('#guide-toggle').getBoundingClientRect();
    return { pointer: getComputedStyle(frame).pointerEvents, proxy: proxy.toJSON(), real: real.toJSON(), host: host.toJSON(), emptyHit: document.elementFromPoint(host.x + 2, host.y + 2)?.id };
  });
  assert.equal(state.pointer, "none");
  assert.equal(await page.locator('#SITE_CONTAINER #fortune-guide-footer-clearance').count(), 0);
  assert.ok((await page.locator('#fortune-guide-footer-clearance').boundingBox()).height >= 78);
  assert.notEqual(state.emptyHit, "fortune-sidecar-frame");
  assert.ok(Math.abs(state.proxy.width - state.real.width) < 1);
  assert.ok(Math.abs(state.proxy.x - (state.host.x + state.real.x)) < 1);
  await launcher.hover();
  await page.waitForFunction(() => document.querySelector('#fortune-sidecar-frame').contentDocument.querySelector('#guide-toggle').classList.contains('is-proxy-hovered'));
  await launcher.click();
  const frame = page.frameLocator('#fortune-sidecar-frame');
  await frame.locator('#guide-panel').waitFor({ state: "visible" });
  assert.equal(await launcher.isVisible(), false);
  assert.equal(await page.locator('#fortune-sidecar-frame').evaluate(e => getComputedStyle(e).pointerEvents), "auto");
  await frame.locator('#guide-close').click();
  await launcher.waitFor({ state: "visible" });
  assert.equal(await launcher.evaluate(e => e === document.activeElement), true);
  assert.equal(requests.filter(r => r.path === "/api/chat").length, 0);
  await page.close();
});
