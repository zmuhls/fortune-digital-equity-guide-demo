import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
import { after, before, test } from "node:test";
import { firefox } from "playwright";

const script = await readFile(new URL("../replica-services.js", import.meta.url), "utf8");
const capture = "2026-09-21T20:47:21.464Z";
let browser;
before(async () => { browser = await firefox.launch({ headless: true }); });
after(async () => { await browser?.close(); });

function fixture(rows, { pagination = false, empty = false } = {}) {
  return `<main><span data-hook="details-locations">Main Office (LIC)<span>|</span>SRP (Bronx)<span>|</span>Fortune Academy (Harlem)</span>
    <div data-hook="book-button-wrapper"><a data-replica-live-action href="https://www.fortunedigitalequity.org/service-page/example" target="_blank">REGISTER on the Digital Equity site</a></div>
    <section data-hook="scheduling-section"><h2 data-hook="scheduling-title">Upcoming Sessions</h2>
    <div data-hook="floating-dropdown-base"></div><div data-hook="scheduling-agenda"><ul>
    ${rows.map(row => `<li data-hook="daily-sessions"><span data-hook="date">${row.date}</span><ul><li data-hook="session-details"><time data-hook="start-time">${row.time || "1:00 PM"}</time><span data-hook="location">${row.location || "Main Office (LIC)"}</span></li></ul></li>`).join("")}</ul></div>
    ${pagination ? '<div data-hook="sessions-pagination"><span>1</span></div>' : ""}
    ${empty ? '<p>No sessions in the next 31 days.</p>' : ""}</section></main>`;
}

async function pageFor(html, { sourceUrl = "https://www.fortunedigitalequity.org/service-page/example", capturedAt = capture, now = "2026-09-24T04:30:00Z" } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.route("**/*", route => route.abort());
  await page.clock.install({ time: new Date(now) });
  await page.setContent(html);
  await page.addScriptTag({ content: script });
  await page.evaluate(options => window.initializeReplicaServices(options), { sourceUrl, capturedAt });
  return page;
}

test("expired sessions leave Upcoming Sessions and cannot offer REGISTER", async () => {
  const page = await pageFor(fixture([{ date: "Tuesday, Sep 22" }]));
  assert.equal(await page.locator('[data-hook="daily-sessions"]').isVisible(), false);
  assert.equal(await page.locator('[data-hook="book-button-wrapper"] a').count(), 0);
  assert.equal(await page.locator('[data-replica-service-closed]').innerText(), "CLOSED");
  assert.match(await page.locator('.fortune-service-status').innerText(), /have ended/);
  assert.equal(await page.getByText('Check live availability.', { exact: true }).getAttribute('href'), "https://www.fortunedigitalequity.org/service-page/example");
  await page.close();
});

test("future captured sessions filter locally using exact source location labels", async () => {
  const page = await pageFor(fixture([{ date: "Friday, Sep 25" }, { date: "Monday, Sep 28", location: "SRP (Bronx)" }]));
  const select = page.getByLabel("Location", { exact: true });
  assert.deepEqual(await select.locator("option").allTextContents(), ["All Locations", "Main Office (LIC)", "SRP (Bronx)", "Fortune Academy (Harlem)"]);
  await select.selectOption("SRP (Bronx)");
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 1);
  assert.match(await page.locator('[data-hook="daily-sessions"]:visible').innerText(), /Monday, Sep 28/);
  await select.selectOption("Fortune Academy (Harlem)");
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 0);
  assert.equal(await page.locator('.fortune-service-empty').isVisible(), true);
  await select.selectOption("");
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 2);
  await page.close();
});

test("capture year and ordered December to January rollover do not resurrect past dates", async () => {
  const page = await pageFor(fixture([{ date: "Friday, Dec 25" }, { date: "Friday, Jan 1" }, { date: "Monday, Jan 4" }]), { now: "2027-01-02T18:00:00Z" });
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 1);
  assert.match(await page.locator('[data-hook="daily-sessions"]:visible').innerText(), /Monday, Jan 4/);
  assert.equal(await page.locator('[data-replica-service-closed]').count(), 0);
  await page.close();
});

test("a session already started today is not offered as upcoming in New York", async () => {
  const page = await pageFor(fixture([{ date: "Thursday, Sep 24", time: "1:00 PM" }, { date: "Thursday, Sep 24", time: "3:00 PM" }]), { now: "2026-09-24T18:00:00Z" });
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 1);
  assert.match(await page.locator('[data-hook="daily-sessions"]:visible').innerText(), /3:00 PM/);
  await page.close();
});

test("a January capture treats the preceding December dates as the previous year", async () => {
  const page = await pageFor(fixture([{ date: "Wednesday, Dec 31" }, { date: "Monday, Jan 5" }]), { capturedAt: "2026-01-02T18:00:00Z", now: "2026-01-03T18:00:00Z" });
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 1);
  assert.match(await page.locator('[data-hook="daily-sessions"]:visible').innerText(), /Monday, Jan 5/);
  await page.close();
});

test("only the actually observed booking destination is upgraded; other routes are not inferred", async () => {
  const page = await pageFor(fixture([{ date: "Friday, Sep 25" }]), { sourceUrl: "https://www.fortunedigitalequity.org/service-page/tech-time-foundations-1" });
  const href = await page.locator('[data-hook="book-button-wrapper"] a').getAttribute('href');
  assert.equal(href, "https://www.fortunedigitalequity.org/booking-calendar/tech-time-foundations-1?timezone=America%2FNew_York&location=&referral=service_details_widget");
  assert.equal(await page.locator('[data-hook="book-button-wrapper"] a').innerText(), "REGISTER");
  await page.close();
  const other = await pageFor(fixture([{ date: "Friday, Sep 25" }]));
  assert.equal(await other.locator('[data-hook="book-button-wrapper"] a').getAttribute('href'), "https://www.fortunedigitalequity.org/service-page/example");
  await other.close();
});

test("missing captured date stays explicit and unparsed, never guessed from today's year", async () => {
  const page = await pageFor(fixture([{ date: "Tuesday, Sep 22" }]), { capturedAt: "", now: "2027-01-02T18:00:00Z" });
  assert.equal(await page.locator('[data-hook="daily-sessions"]').isVisible(), true);
  assert.match(await page.locator('.fortune-service-status').innerText(), /This is a captured schedule/);
  assert.equal(await page.locator('[data-hook="scheduling-title"]').innerText(), "Captured Sessions");
  assert.equal(await page.locator('[data-hook="book-button-wrapper"] a').innerText(), "CHECK LIVE AVAILABILITY");
  await page.close();
});

test("uncaptured pagination hands off honestly and relative availability is timestamp-qualified", async () => {
  const page = await pageFor(fixture([], { pagination: true, empty: true }));
  assert.equal(await page.locator('[data-hook="sessions-pagination"]').innerText(), "More dates on live site");
  assert.equal(await page.getByText('More dates on live site').getAttribute('href'), "https://www.fortunedigitalequity.org/service-page/example");
  assert.doesNotMatch(await page.locator('main').innerText(), /next 31 days/);
  assert.match(await page.locator('.fortune-service-status').innerText(), /Sep 21, 2026/);
  const again = await page.evaluate(() => window.initializeReplicaServices({ sourceUrl: "https://www.fortunedigitalequity.org/service-page/example" }));
  assert.equal(again, false);
  assert.equal(await page.locator('select').count(), 1);
  await page.close();
});

test("actual captured AI Safety markup closes the past September 22 session", async () => {
  const compressed = await readFile(new URL("../replica-snapshots/service-service-page-ai-safety-in-2026-94d528fe.html.gz", import.meta.url));
  const page = await pageFor(gunzipSync(compressed).toString(), { sourceUrl: "https://www.fortunedigitalequity.org/service-page/ai-safety-in-2026" });
  assert.equal(await page.locator('[data-hook="daily-sessions"]:visible').count(), 0);
  assert.equal(await page.locator('[data-replica-service-closed]').count(), 2);
  assert.equal(await page.locator('[data-hook="book-button-wrapper"] a').count(), 0);
  assert.deepEqual(await page.getByLabel('Location', { exact: true }).locator('option').allTextContents(), ["All Locations", "Main Office (LIC)", "SRP (Bronx)", "Fortune Academy (Harlem)"]);
  await page.close();
});

test("every captured service page initializes without changing its description or inventing a booking route", async () => {
  const manifest = JSON.parse(await readFile(new URL("../replica-manifest.json", import.meta.url), "utf8"));
  const services = manifest.pages.filter(page => page.path.startsWith("/service-page/"));
  assert.equal(services.length, 88);
  const page = await browser.newPage();
  await page.route("**/*", route => route.abort());
  await page.clock.install({ time: new Date("2026-09-24T04:30:00Z") });
  for (const service of services) {
    const compressed = await readFile(new URL(`../${service.file}`, import.meta.url));
    await page.setContent(gunzipSync(compressed).toString());
    const before = await page.locator('[data-hook="description"]').innerText();
    await page.addScriptTag({ content: script });
    assert.equal(await page.evaluate(options => window.initializeReplicaServices(options), { sourceUrl: service.url, capturedAt: manifest.captured_at }), true, service.path);
    assert.equal(await page.locator('[data-hook="description"]').innerText(), before, service.path);
    const destinations = await page.locator('main a[data-replica-live-action]').evaluateAll(links => links.map(link => link.href));
    for (const destination of destinations) {
      assert.equal(new URL(destination).hostname, "www.fortunedigitalequity.org", service.path);
      if (new URL(destination).pathname.startsWith("/booking-calendar/")) assert.equal(service.path, "/service-page/tech-time-foundations-1");
      else assert.equal(new URL(destination).pathname, new URL(service.url).pathname, service.path);
    }
  }
  await page.close();
});
