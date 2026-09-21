#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { firefox } from "playwright";


const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BASE = (process.argv[2] || "https://guide-api-production-a1a1.up.railway.app").replace(/\/$/, "");
const OUTPUT = path.resolve(process.argv[3] || path.join(ROOT, "output/playwright/replica-route-audit.json"));
const PROFILES = [
  { name: "desktop", viewport: { width: 1440, height: 1200 } },
  { name: "small-laptop", viewport: { width: 1024, height: 768 } },
  { name: "tablet", viewport: { width: 768, height: 1024 } },
  { name: "mobile", viewport: { width: 375, height: 812 } },
];


function routePath(url) {
  return new URL(url).pathname.replace(/\/$/, "") || "/";
}


async function auditProfile(browser, pages, profile) {
  const context = await browser.newContext({
    viewport: profile.viewport,
    locale: "en-US",
    timezoneId: "America/New_York",
    colorScheme: "light",
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  const results = [];

  for (let index = 0; index < pages.length; index += 1) {
    const indexed = pages[index];
    const route = routePath(indexed.url);
    const url = `${BASE}${route === "/" ? "/" : route}?guide=0`;
    const consoleErrors = [];
    const onConsole = (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    };
    page.on("console", onConsole);
    let status = null;
    let error = null;
    let metrics = {};
    try {
      const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45_000 });
      status = response?.status() ?? null;
      await page.evaluate(async () => {
        if (document.fonts?.ready) await document.fonts.ready;
      });
      await page.waitForTimeout(100);
      metrics = await page.evaluate(({ expectedSource }) => {
        const root = document.documentElement;
        const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
        const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
        const missingFragmentTargets = [...document.querySelectorAll('a[href*="#"]')]
          .map((anchor) => {
            try {
              const target = new URL(anchor.href, location.href);
              if (target.origin !== location.origin || target.pathname.replace(/\/$/, "") !== location.pathname.replace(/\/$/, "")) return null;
              return target.hash && !document.getElementById(decodeURIComponent(target.hash.slice(1)))
                ? { label: anchor.getAttribute("aria-label") || anchor.textContent?.trim() || "", href: anchor.getAttribute("href") }
                : null;
            } catch (_error) {
              return { label: anchor.textContent?.trim() || "", href: anchor.getAttribute("href") };
            }
          })
          .filter(Boolean);
        const brokenImages = [...document.images]
          .filter((image) => {
            const style = getComputedStyle(image);
            return (
              image.getClientRects().length > 0 &&
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              style.opacity !== "0" &&
              image.complete &&
              image.naturalWidth === 0
            );
          })
          .map((image) => ({ alt: image.alt, src: image.currentSrc || image.src }));
        const deadControls = [...document.querySelectorAll("button,input,textarea,select")]
          .filter((control) => !control.closest("#fortune-sidecar-host"))
          .map((control) => control.getAttribute("aria-label") || control.textContent?.trim() || control.localName);
        return {
          title: document.title,
          marker: document.documentElement.dataset.fortuneVisualMirror === "true",
          source: document.querySelector('meta[name="fortune-replica-source"]')?.content || null,
          sourceMatches: document.querySelector('meta[name="fortune-replica-source"]')?.content === expectedSource,
          revision: document.querySelector('meta[http-equiv="X-Wix-Published-Version"]')?.content || null,
          clientWidth: root.clientWidth,
          scrollWidth: root.scrollWidth,
          horizontalOverflow: Math.max(0, root.scrollWidth - root.clientWidth),
          duplicateIds,
          missingFragmentTargets,
          brokenImages,
          deadControls,
        };
      }, { expectedSource: indexed.url });
    } catch (caught) {
      error = String(caught?.message || caught);
    } finally {
      page.off("console", onConsole);
    }
    const failures = [];
    if (status !== 200) failures.push(`HTTP ${status}`);
    if (error) failures.push(error);
    if (!metrics.marker) failures.push("mirror marker missing");
    if (!metrics.sourceMatches) failures.push("source identity mismatch");
    if (!metrics.title) failures.push("title missing");
    if (metrics.missingFragmentTargets?.length) failures.push(`${metrics.missingFragmentTargets.length} missing fragment targets`);
    if (metrics.brokenImages?.length) failures.push(`${metrics.brokenImages.length} broken visible images`);
    if (metrics.deadControls?.length) failures.push(`${metrics.deadControls.length} inert source controls`);
    if (metrics.horizontalOverflow > 1) failures.push(`${metrics.horizontalOverflow}px horizontal overflow`);
    results.push({ route, status, ...metrics, consoleErrors, failures });
    process.stdout.write(`[${profile.name} ${index + 1}/${pages.length}] ${route} ${failures.length ? "FAIL" : "ok"}\n`);
  }

  await context.close();
  return results;
}


const index = JSON.parse(await readFile(path.join(ROOT, "site-index.json"), "utf8"));
const browser = await firefox.launch({ headless: true });
const profiles = {};
try {
  for (const profile of PROFILES) profiles[profile.name] = await auditProfile(browser, index.pages, profile);
} finally {
  await browser.close();
}

const report = {
  audited_at: new Date().toISOString(),
  base_url: BASE,
  route_count: index.pages.length,
  profiles,
  summary: Object.fromEntries(PROFILES.map((profile) => {
    const rows = profiles[profile.name];
    return [profile.name, {
      passed: rows.filter((row) => row.failures.length === 0).length,
      failed: rows.filter((row) => row.failures.length > 0).length,
      overflow_routes: rows.filter((row) => row.horizontalOverflow > 0).length,
      console_error_routes: rows.filter((row) => row.consoleErrors.length > 0).length,
    }];
  })),
};
await mkdir(path.dirname(OUTPUT), { recursive: true });
await writeFile(OUTPUT, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify(report.summary, null, 2));
if (Object.values(report.summary).some((summary) => summary.failed > 0)) process.exitCode = 1;
