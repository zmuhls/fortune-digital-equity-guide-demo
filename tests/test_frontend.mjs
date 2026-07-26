import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const TESTS = dirname(fileURLToPath(import.meta.url));
const DEMO = dirname(TESTS);
const Core = require(join(DEMO, "guide-core.js"));
const index = JSON.parse(readFileSync(join(DEMO, "site-index.json"), "utf8"));
const pages = index.pages;
const byPath = new Map(pages.map(page => [new URL(page.url).pathname, page]));

test("canonical URLs stay on the approved public host", () => {
  assert.equal(Core.canonicalUrl("https://fortunedigitalequity.org/devices/?x=1#top"), "https://www.fortunedigitalequity.org/devices");
  assert.equal(Core.canonicalUrl("/about/"), "https://www.fortunedigitalequity.org/about");
  assert.equal(Core.canonicalUrl("https://example.com/devices"), "");
  assert.equal(Core.pathFor("https://www.fortunedigitalequity.org/"), "/");
});

test("all 184 routes receive one of the reviewed page families", () => {
  const counts = {};
  for (const page of pages) {
    const family = Core.pageFamily(page);
    counts[family] = (counts[family] || 0) + 1;
  }
  assert.deepEqual(counts, {
    program: 4,
    excluded: 17,
    action: 6,
    directory: 8,
    support: 2,
    event: 7,
    archive: 13,
    news: 7,
    service: 120,
  });
  assert.equal(Object.values(counts).reduce((sum, value) => sum + value, 0), 184);
});

test("every page has a tailored heading, placeholder, and exactly two prompts", () => {
  for (const page of pages) {
    const starter = Core.starterFor(page);
    assert.ok(starter.heading.length > 8, page.url);
    assert.ok(starter.placeholder.endsWith("?"), page.url);
    assert.equal(starter.suggestions.length, 2, page.url);
    assert.equal(new Set(starter.suggestions).size, 2, page.url);
  }
  assert.equal(Core.starterFor(byPath.get("/devices")).placeholder, "Do you need a device or help using one?");
  assert.equal(Core.starterFor(byPath.get("/calendar")).suggestions[0], "Where and when are current classes?");
  assert.equal(Core.starterFor(byPath.get("/service-page/intro-to-computers")).suggestions[0], "What does this class cover?");
});

test("current-page evidence is recognized before a wider search", () => {
  const devices = byPath.get("/devices");
  const trainings = byPath.get("/trainings");
  assert.equal(Core.currentPageCanAnswer("Can I get a free laptop?", devices), true);
  assert.equal(Core.currentPageCanAnswer("Can I get a free laptop?", trainings), false);
  assert.equal(Core.currentPageCanAnswer("What does this page say?", trainings), true);
  assert.equal(Core.currentPageCanAnswer("What is the zzyzx quasar permit policy?", trainings), false);
});

test("excluded, archived, and partial records can never become current-page evidence", () => {
  for (const page of pages.filter(page => page.authority !== "answer" || Number(page.status) !== 200)) {
    assert.equal(Core.currentPageCanAnswer("What does this page say?", page), false, page.url);
  }
});

test("six-digit Fortune ID patterns are detected after Unicode normalization", () => {
  for (const value of [
    "123456",
    "123-456",
    "123 456",
    "１２３４５６",
    "١٢٣٤٥٦",
    "My Fortune ID is 654321",
  ]) {
    assert.equal(Core.personalInformationDetected(value), true, value);
  }
  assert.equal(Core.personalInformationDetected("Workshop 12345"), false);
  assert.equal(Core.personalInformationDetected("Workshop 1234567"), false);
});

test("other obvious personal-information forms are held", () => {
  for (const value of [
    "Email me at person@example.com",
    "My SSN is 123-45-6789",
    "My case number is ABC-12",
    "My address is 123 Example Street",
    "My diagnosis is private",
  ]) {
    assert.equal(Core.personalInformationDetected(value), true, value);
  }
});

test("redaction removes every six-digit representation from display text", () => {
  for (const value of ["123456", "123-456", "123 456", "１２３４５６", "١٢٣٤٥٦"]) {
    const redacted = Core.redactSixDigitValues(`ID ${value}`);
    assert.equal(redacted.includes("123456"), false, value);
    assert.equal(redacted.includes("123-456"), false, value);
    assert.match(redacted, /\[six-digit ID removed\]/);
  }
});

test("mock hrefs preserve the repository base for root and nested routes", () => {
  const about = Core.canonicalUrl("/about");
  const root = Core.canonicalUrl("/");
  const known = new Set([about, root]);
  assert.equal(Core.hrefFor(about, { staticRoutes: false, knownUrls: known }), "?page=%2Fabout");
  assert.equal(Core.hrefFor(about, { staticRoutes: true, assetBase: "../../", knownUrls: known }), "../../about/");
  assert.equal(Core.hrefFor(root, { staticRoutes: true, assetBase: "../../", knownUrls: known }), "../../");
  assert.equal(Core.hrefFor("https://example.com/nope", { staticRoutes: true, assetBase: "../../", knownUrls: known }), "https://example.com/nope");
});

test("destination labels stay grammatical", () => {
  assert.equal(Core.destinationLabel("Regular Workshops | FS Digital Equity"), "Go to Regular Workshops");
  assert.equal(Core.destinationLabel("Confirm eligibility with staff"), "Confirm eligibility with staff");
  assert.equal(Core.destinationLabel("Contact Digital Equity"), "Contact Digital Equity");
});

test("public text cleanup removes duplicated sentences and source-title suffixes", () => {
  assert.equal(Core.cleanText("Great , start here.  Great , start here."), "Great, start here.");
  assert.equal(Core.cleanTitle("Devices | FS Digital Equity"), "Devices");
});

test("viewer mode defaults to admin locally and public on deployed hosts", () => {
  assert.equal(Core.viewerMode("127.0.0.1"), "admin");
  assert.equal(Core.viewerMode("localhost"), "admin");
  assert.equal(Core.viewerMode("zmuhls.github.io"), "public");
  assert.equal(Core.viewerMode("www.fortunedigitalequity.org"), "public");
  assert.equal(Core.viewerMode("zmuhls.github.io", "admin"), "admin");
  assert.equal(Core.viewerMode("127.0.0.1", "public"), "public");
});
