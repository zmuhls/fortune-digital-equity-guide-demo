#!/usr/bin/env node

/**
 * Capture inert, deterministic HTML snapshots of every indexed public route.
 *
 * Firefox runs the live page long enough for Wix and lazy media to render. The
 * resulting main-frame document is then stripped of executable and
 * data-collecting surfaces before it is serialized and compressed.
 */

import { createHash, randomBytes } from "node:crypto";
import { constants as fsConstants, realpathSync } from "node:fs";
import {
  access,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { hostname } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

import { gzipSync } from "fflate";
import { firefox } from "playwright";


const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, "..");
export const SOURCE_ORIGIN = "https://www.fortunedigitalequity.org";
export const VIEWPORT = Object.freeze({ width: 1440, height: 1200 });
export const FIXED_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) " +
  "Gecko/20100101 Firefox/128.0 FortuneReplicaCapture/1.0";
export const DEFAULT_CONCURRENCY = 2;
export const DEFAULT_NAVIGATION_TIMEOUT_MS = 90_000;
export const MAX_TRANSIENT_NAVIGATION_ATTEMPTS = 3;
export const MAIN_CONTENT_SELECTOR = '#PAGES_CONTAINER,main,[data-main-content="true" i]';
export const WIX_ACCORDION_HEADER_SELECTOR =
  'button[data-hook="accordion-item-header"][aria-controls]';
export const MAX_PROGRESSIVE_COLLECTION_EXPANSIONS = 24;
export const CALENDAR_STATIC_HORIZON_EXPANSIONS = 9;
export const CALENDAR_POPUP_DISMISSAL_TIMEOUT_MS = 5_000;

// This exists only while Firefox is collecting hidden panel content. It is
// removed when the native static disclosure is written into the document.
const CAPTURE_DISCLOSURE_ATTRIBUTE = "data-replica-capture-disclosure";
const CAPTURE_COLLECTION_ATTRIBUTE = "data-replica-capture-collection-control";

const SAFE_ID = /^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$/i;
const FORBIDDEN_MARKUP = [
  [/<\s*script\b/i, "script element"],
  [/<\s*(?:template|noscript)\b/i, "template or noscript element"],
  [/<\s*(?:object|embed|iframe|form)\b/i, "active embedded or form element"],
  [/<\s*meta\b[^>]*http-equiv\s*=\s*["']?\s*refresh\b/i, "meta refresh"],
  [/\ssrcdoc\s*=/i, "iframe srcdoc"],
  [/\son[a-z][a-z0-9_-]*\s*=/i, "inline event handler"],
  [/(?:href|action|formaction|xlink:href)\s*=\s*["']?\s*(?:javascript|vbscript|data)\s*:/i, "executable navigation URL"],
  [/(?:src)\s*=\s*["']?\s*(?:javascript|vbscript)\s*:/i, "executable source URL"],
  [/wix-(?:essential-)?viewer-model/i, "Wix viewer model"],
  [/(?:x-)?xsrf-token/i, "XSRF token"],
  [/["'](?:sessionToken|accessToken)["']\s*[:=]/i, "session or access token"],
  [/--cookie-banner-/i, "transient cookie-banner style"],
  [/>\s*An error occurred\.\s*Try again later\s*</i, "transient Wix form error state"],
  [/>\s*Your content has been submitted\s*</i, "transient Wix form success state"],
  [/>\s*Widget Didn[’']t Load\s*</i, "transient Wix widget error state"],
  [/<button\b(?![^>]*\bdisabled(?:\s|=|>))[^>]*>/i, "active button"],
  [/<(?:input|textarea|select)\b(?![^>]*\bdisabled(?:\s|=|>))[^>]*>/i, "active form control"],
];


export class CaptureError extends Error {
  constructor(message) {
    super(message);
    this.name = "CaptureError";
  }
}


function valueAfter(argv, index, option) {
  if (index + 1 >= argv.length || argv[index + 1].startsWith("--")) {
    throw new CaptureError(`${option} requires a value`);
  }
  return argv[index + 1];
}


function positiveInteger(value, option) {
  if (!/^\d+$/.test(value) || Number(value) < 1 || !Number.isSafeInteger(Number(value))) {
    throw new CaptureError(`${option} must be a positive integer`);
  }
  return Number(value);
}


export function parseArgs(argv) {
  const options = {
    indexPath: path.join(ROOT, "site-index.json"),
    outputDir: ROOT,
    concurrency: DEFAULT_CONCURRENCY,
    navigationTimeoutMs: DEFAULT_NAVIGATION_TIMEOUT_MS,
    routes: [],
    limit: null,
    allowedStatuses: new Map(),
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
      continue;
    }
    if (argument === "--index") {
      options.indexPath = path.resolve(valueAfter(argv, index, argument));
      index += 1;
      continue;
    }
    if (argument === "--output-dir") {
      options.outputDir = path.resolve(valueAfter(argv, index, argument));
      index += 1;
      continue;
    }
    if (argument === "--concurrency") {
      options.concurrency = positiveInteger(valueAfter(argv, index, argument), argument);
      index += 1;
      continue;
    }
    if (argument === "--navigation-timeout-ms") {
      options.navigationTimeoutMs = positiveInteger(valueAfter(argv, index, argument), argument);
      index += 1;
      continue;
    }
    if (argument === "--route") {
      options.routes.push(valueAfter(argv, index, argument));
      index += 1;
      continue;
    }
    if (argument === "--limit") {
      options.limit = positiveInteger(valueAfter(argv, index, argument), argument);
      index += 1;
      continue;
    }
    if (argument === "--allow-status") {
      const rule = valueAfter(argv, index, argument);
      const separator = rule.lastIndexOf("=");
      if (separator < 1 || !/^\d{3}$/.test(rule.slice(separator + 1))) {
        throw new CaptureError("--allow-status must use URL=STATUS");
      }
      const url = canonicalSourceUrl(rule.slice(0, separator));
      const status = Number(rule.slice(separator + 1));
      if (status < 100 || status > 599) {
        throw new CaptureError("--allow-status must contain an HTTP status from 100 through 599");
      }
      if (status === 200) {
        throw new CaptureError("--allow-status is only for an expected non-200 response");
      }
      if (options.allowedStatuses.has(url)) {
        throw new CaptureError(`duplicate --allow-status rule for ${url}`);
      }
      options.allowedStatuses.set(url, status);
      index += 1;
      continue;
    }
    throw new CaptureError(`unknown option: ${argument}`);
  }
  return options;
}


export function helpText() {
  return `Usage: node scripts/capture_replica.mjs [options]

Capture every route in site-index.json with Firefox, sanitize the rendered
main-frame HTML, and atomically publish deterministic gzip snapshots.

Options:
  --index PATH                 Read a different site index.
  --output-dir PATH            Put replica-manifest.json and replica-snapshots/
                               under PATH. Required for a partial smoke run.
  --concurrency NUMBER         Capture this many routes at once (default: 2).
  --navigation-timeout-ms MS   Set the per-route navigation timeout (default: 90000).
  --route URL_OR_PATH          Capture one indexed route; repeat to select more.
  --limit NUMBER               Capture only the first NUMBER selected routes.
  --allow-status URL=STATUS    Permit one indexed URL to return an expected
                               non-200 status; repeat for additional exceptions.
  -h, --help                   Show this help text.

Examples:
  npm run capture:replica
  npm run capture:replica -- --concurrency 3
  npm run capture:replica -- --route / --output-dir /tmp/fortune-replica-smoke
`;
}


export function routePath(value) {
  let url;
  try {
    url = new URL(value, `${SOURCE_ORIGIN}/`);
  } catch (error) {
    throw new CaptureError(`invalid source URL: ${value} (${error.message})`);
  }
  if (
    url.origin !== SOURCE_ORIGIN ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new CaptureError(`route must be a public ${SOURCE_ORIGIN} URL without query or fragment: ${value}`);
  }
  if (/%(?:2f|5c)/i.test(url.pathname)) {
    throw new CaptureError(`route contains an encoded path separator: ${value}`);
  }
  if (/%2e/i.test(String(value))) {
    throw new CaptureError(`route contains an encoded dot segment: ${value}`);
  }
  let decodedPath;
  try {
    decodedPath = decodeURI(url.pathname);
  } catch (_error) {
    throw new CaptureError(`route contains invalid percent encoding: ${value}`);
  }
  const normalizedPath = decodedPath.replace(/\/{2,}/g, "/").replace(/\/+$/, "") || "/";
  if (normalizedPath.split("/").includes("..") || /(?:^|\/)\.\.(?:\/|$)/.test(String(value))) {
    throw new CaptureError(`route contains path traversal: ${value}`);
  }
  return normalizedPath;
}


export function canonicalSourceUrl(value) {
  const pagePath = routePath(value);
  return `${SOURCE_ORIGIN}${pagePath === "/" ? "/" : pagePath}`;
}


export function sameFilesystemPath(left, right) {
  const canonical = (value) => {
    try {
      return realpathSync.native(path.resolve(value));
    } catch (error) {
      if (error.code === "ENOENT") return path.resolve(value);
      throw error;
    }
  };
  return canonical(left) === canonical(right);
}


export function validateIndex(document) {
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new CaptureError("site index must be a JSON object");
  }
  if (!Array.isArray(document.pages) || document.pages.length === 0) {
    throw new CaptureError("site index must contain a non-empty pages array");
  }
  if (document.unique_urls !== document.pages.length) {
    throw new CaptureError(
      `site index is incomplete: unique_urls=${document.unique_urls}, pages=${document.pages.length}`,
    );
  }

  const ids = new Set();
  const urls = new Set();
  const paths = new Set();
  const routes = document.pages.map((page, index) => {
    if (!page || typeof page !== "object") {
      throw new CaptureError(`page ${index + 1} is not an object`);
    }
    if (typeof page.id !== "string" || !SAFE_ID.test(page.id) || page.id.includes("..")) {
      throw new CaptureError(`page ${index + 1} has an unsafe id: ${page.id}`);
    }
    const pagePath = routePath(page.url);
    const url = canonicalSourceUrl(page.url);
    if (page.url !== url) {
      throw new CaptureError(`page ${page.id} does not use the canonical source URL: ${page.url}`);
    }
    if (ids.has(page.id)) throw new CaptureError(`duplicate page id: ${page.id}`);
    if (urls.has(url)) throw new CaptureError(`duplicate page URL: ${url}`);
    if (paths.has(pagePath)) throw new CaptureError(`duplicate route path: ${pagePath}`);
    ids.add(page.id);
    urls.add(url);
    paths.add(pagePath);
    return Object.freeze({ id: page.id, url, path: pagePath });
  });
  return routes;
}


function selectionKey(value) {
  if (value.startsWith("/")) return routePath(value);
  return canonicalSourceUrl(value);
}


export function selectRoutes(routes, options) {
  let selected = routes;
  if (options.routes.length > 0) {
    const requested = new Set(options.routes.map(selectionKey));
    selected = routes.filter((route) => requested.has(route.path) || requested.has(route.url));
    const found = new Set(selected.flatMap((route) => [route.path, route.url]));
    const missing = [...requested].filter((value) => !found.has(value));
    if (missing.length > 0) {
      throw new CaptureError(`requested route is absent from site-index.json: ${missing.join(", ")}`);
    }
  }
  if (options.limit !== null) selected = selected.slice(0, options.limit);
  if (selected.length === 0) throw new CaptureError("route selection is empty");

  const partial = selected.length !== routes.length;
  const canonicalOutput = sameFilesystemPath(options.outputDir, ROOT);
  if (partial && canonicalOutput) {
    throw new CaptureError("a partial capture requires --output-dir different from the repository root");
  }
  if (
    canonicalOutput &&
    !sameFilesystemPath(options.indexPath, path.join(ROOT, "site-index.json"))
  ) {
    throw new CaptureError("an alternate --index requires --output-dir different from the repository root");
  }
  if (canonicalOutput && options.allowedStatuses.size > 0) {
    throw new CaptureError("a non-200 status exception requires --output-dir different from the repository root");
  }
  for (const url of options.allowedStatuses.keys()) {
    if (!selected.some((route) => route.url === url)) {
      throw new CaptureError(`--allow-status URL is absent from the selected routes: ${url}`);
    }
  }
  return { selected, partial };
}


export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}


export async function deterministicGzip(bytes) {
  return Buffer.from(gzipSync(bytes, { level: 9, mtime: 0 }));
}


export function capturedAt(environment = process.env, now = new Date()) {
  const epoch = environment.SOURCE_DATE_EPOCH;
  if (epoch === undefined || epoch === "") return now.toISOString();
  if (!/^\d+$/.test(epoch)) {
    throw new CaptureError("SOURCE_DATE_EPOCH must be a non-negative integer");
  }
  const timestamp = new Date(Number(epoch) * 1000);
  if (Number.isNaN(timestamp.getTime())) {
    throw new CaptureError("SOURCE_DATE_EPOCH is outside the supported date range");
  }
  return timestamp.toISOString();
}


export function assertSanitized(html) {
  for (const [pattern, description] of FORBIDDEN_MARKUP) {
    if (pattern.test(html)) {
      throw new CaptureError(`sanitized snapshot still contains a ${description}`);
    }
  }
  if (!/<meta\b[^>]*name=["']robots["'][^>]*content=["'][^"']*noindex/i.test(html)) {
    throw new CaptureError("sanitized snapshot is missing the noindex directive");
  }
  if (!/<meta\b[^>]*name=["']referrer["'][^>]*content=["']no-referrer["']/i.test(html)) {
    throw new CaptureError("sanitized snapshot is missing the no-referrer directive");
  }
}


function openingTags(html, tagName) {
  return html.match(new RegExp(`<${tagName}\\b[^>]*>`, "gi")) || [];
}


function hasMarker(tag, name) {
  return new RegExp(`\\b${name}=(?:["']true["']|true)(?:\\s|>|$)`, "i").test(tag);
}


/**
 * The live Wix FAQ widget mounts one response at a time. A static capture
 * must instead contain native, open disclosures for each item it collected.
 * This is intentionally a string-level release gate so an incomplete browser
 * interaction can never quietly replace a full FAQ with closed, inert buttons.
 */
export function assertStaticContentMaterialized(html, expected = {}) {
  const expectedDisclosures = Number(expected.disclosures || 0);
  const expectedMenus = Number(expected.navigationMenus || 0);
  if (!Number.isSafeInteger(expectedDisclosures) || expectedDisclosures < 0) {
    throw new CaptureError("static disclosure expectation must be a non-negative integer");
  }
  if (!Number.isSafeInteger(expectedMenus) || expectedMenus < 0) {
    throw new CaptureError("static navigation-menu expectation must be a non-negative integer");
  }

  const staticDisclosureTags = openingTags(html, "details")
    .filter((tag) => hasMarker(tag, "data-replica-static-disclosure"));
  const staticContentTags = openingTags(html, "div")
    .filter((tag) => hasMarker(tag, "data-replica-static-content"));
  if (staticDisclosureTags.length !== expectedDisclosures) {
    throw new CaptureError(
      `snapshot contains ${staticDisclosureTags.length} static disclosures; expected ${expectedDisclosures}`,
    );
  }
  if (staticContentTags.length !== expectedDisclosures) {
    throw new CaptureError(
      `snapshot contains ${staticContentTags.length} static disclosure bodies; expected ${expectedDisclosures}`,
    );
  }
  if (staticDisclosureTags.some((tag) => !/\bopen(?:\s|=|>)/i.test(tag))) {
    throw new CaptureError("static disclosure is not open in its capture state");
  }
  if (/<button\b[^>]*\bdata-hook=(?:"accordion-item-header"|'accordion-item-header')[^>]*>/i.test(html)) {
    throw new CaptureError("Wix accordion header remains instead of a native static disclosure");
  }

  const staticMenuTags = openingTags(html, "details")
    .filter((tag) => hasMarker(tag, "data-replica-static-menu"));
  if (staticMenuTags.length !== expectedMenus) {
    throw new CaptureError(
      `snapshot contains ${staticMenuTags.length} static navigation menus; expected ${expectedMenus}`,
    );
  }
  for (const tag of staticMenuTags) {
    if (/\baria-hidden=(?:"true"|'true')/i.test(tag) || /display\s*:\s*none/i.test(tag)) {
      throw new CaptureError("static navigation menu remains hidden");
    }
  }
}


/** This function is passed directly to page.evaluate. */
export function progressiveCollectionMetric() {
  const root = document.querySelector('#PAGES_CONTAINER,main,[data-main-content="true" i]');
  if (!root) {
    return { visible_text_characters: 0, links: 0, service_page_links: 0, images: 0 };
  }
  const text = (root.innerText || "").replace(/\s+/g, " ").trim();
  const visibleLinks = [...root.querySelectorAll("a[href]")].filter((anchor) => {
    const style = getComputedStyle(anchor);
    return anchor.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden";
  });
  const visibleImages = [...root.querySelectorAll("img")].filter((image) => {
    const style = getComputedStyle(image);
    return (
      image.getClientRects().length > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Boolean(image.currentSrc || image.getAttribute("src"))
    );
  });
  return {
    visible_text_characters: text.length,
    links: visibleLinks.length,
    service_page_links: visibleLinks.filter((anchor) => /\/service-page\//.test(anchor.href)).length,
    images: visibleImages.length,
  };
}


/**
 * A collection may add cards, text links, or (for gallery widgets) media.
 * Keep this pure so the policy is testable outside the browser capture.
 */
export function collectionMetricAdvanced(before, after) {
  return (
    Number(after?.visible_text_characters || 0) > Number(before?.visible_text_characters || 0) ||
    Number(after?.links || 0) > Number(before?.links || 0) ||
    Number(after?.service_page_links || 0) > Number(before?.service_page_links || 0) ||
    Number(after?.images || 0) > Number(before?.images || 0)
  );
}


/**
 * Mark precisely one visible, content-only collection control. The matching
 * deliberately excludes filters, registration buttons, and arbitrary action
 * controls: only reviewed public Wix collection widgets are safe to activate
 * during a static capture.
 */
export function markNextProgressiveCollectionControl() {
  document.querySelectorAll("[data-replica-capture-collection-control]").forEach((element) => {
    element.removeAttribute("data-replica-capture-collection-control");
  });
  const root = document.querySelector('#PAGES_CONTAINER,main,[data-main-content="true" i]');
  if (!root) return null;
  const candidates = [...root.querySelectorAll("button,[role='button']")];
  for (const candidate of candidates) {
    const label = (candidate.getAttribute("aria-label") || candidate.innerText || candidate.textContent || "")
      .replace(/\s+/g, " ")
      .trim();
    const style = getComputedStyle(candidate);
    const visible =
      candidate.getClientRects().length > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0";
    const disabled =
      candidate.disabled === true ||
      candidate.getAttribute("aria-disabled") === "true" ||
      candidate.hasAttribute("inert");
    const hook = candidate.getAttribute("data-hook") || "";
    const safeCollectionWidget =
      hook === "load-services-button-button" ||
      hook === "daily-agenda-load-more-button" ||
      hook === "show-more";
    if (visible && !disabled && safeCollectionWidget && /^(?:load|show) more$/i.test(label)) {
      candidate.setAttribute("data-replica-capture-collection-control", "next");
      return { label, hook };
    }
  }
  return null;
}


/** This function is serialized into Firefox by page.waitForFunction. */
export function progressiveCollectionHasAdvanced(before) {
  const root = document.querySelector('#PAGES_CONTAINER,main,[data-main-content="true" i]');
  if (!root) return false;
  const text = (root.innerText || "").replace(/\s+/g, " ").trim();
  const visibleLinks = [...root.querySelectorAll("a[href]")].filter((anchor) => {
    const style = getComputedStyle(anchor);
    return anchor.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden";
  });
  const visibleImages = [...root.querySelectorAll("img")].filter((image) => {
    const style = getComputedStyle(image);
    return (
      image.getClientRects().length > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Boolean(image.currentSrc || image.getAttribute("src"))
    );
  });
  const metric = {
    visible_text_characters: text.length,
    links: visibleLinks.length,
    service_page_links: visibleLinks.filter((anchor) => /\/service-page\//.test(anchor.href)).length,
    images: visibleImages.length,
  };
  return (
    metric.visible_text_characters > before.visible_text_characters ||
    metric.links > before.links ||
    metric.service_page_links > before.service_page_links ||
    metric.images > before.images
  );
}


/**
 * This function is serialized into Firefox by page.waitForFunction. A gallery
 * can already have all of its lazy media materialized by the bounded scroll
 * hydration step; its final Load More click then retires the control without
 * increasing the metric. That is a completed collection, not a no-op. An
 * enabled, remaining control with no metric increase still fails the capture.
 */
export function progressiveCollectionTransitioned(before) {
  const root = document.querySelector('#PAGES_CONTAINER,main,[data-main-content="true" i]');
  if (!root) return false;
  const text = (root.innerText || "").replace(/\s+/g, " ").trim();
  const visibleLinks = [...root.querySelectorAll("a[href]")].filter((anchor) => {
    const style = getComputedStyle(anchor);
    return anchor.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden";
  });
  const visibleImages = [...root.querySelectorAll("img")].filter((image) => {
    const style = getComputedStyle(image);
    return (
      image.getClientRects().length > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Boolean(image.currentSrc || image.getAttribute("src"))
    );
  });
  const metric = {
    visible_text_characters: text.length,
    links: visibleLinks.length,
    service_page_links: visibleLinks.filter((anchor) => /\/service-page\//.test(anchor.href)).length,
    images: visibleImages.length,
  };
  const advanced =
    metric.visible_text_characters > before.visible_text_characters ||
    metric.links > before.links ||
    metric.service_page_links > before.service_page_links ||
    metric.images > before.images;
  const control = document.querySelector(
    '[data-replica-capture-collection-control="next"]',
  );
  const retired =
    !control ||
    control.disabled === true ||
    control.getAttribute("aria-disabled") === "true" ||
    control.hasAttribute("inert");
  return advanced || retired;
}


/**
 * A completed collection can retain a disabled visual Load More control. It
 * has no remaining source content to reveal, so omit it from the static copy
 * rather than publishing a dead control after sanitization.
 */
export function removeExhaustedProgressiveCollectionControls() {
  const root = document.querySelector('#PAGES_CONTAINER,main,[data-main-content="true" i]');
  if (!root) return 0;
  let removed = 0;
  root.querySelectorAll("button,[role='button']").forEach((candidate) => {
    const label = (candidate.getAttribute("aria-label") || candidate.innerText || candidate.textContent || "")
      .replace(/\s+/g, " ")
      .trim();
    const disabled =
      candidate.disabled === true ||
      candidate.getAttribute("aria-disabled") === "true" ||
      candidate.hasAttribute("inert");
    const hook = candidate.getAttribute("data-hook") || "";
    const safeCollectionWidget =
      hook === "load-services-button-button" ||
      hook === "daily-agenda-load-more-button" ||
      hook === "show-more";
    if (disabled && safeCollectionWidget && /^(?:load|show) more$/i.test(label)) {
      candidate.remove();
      removed += 1;
    }
  });
  document.querySelectorAll("[data-replica-capture-collection-control]").forEach((element) => {
    element.removeAttribute("data-replica-capture-collection-control");
  });
  return removed;
}


/**
 * The live calendar offers an unbounded future agenda. Preserve a useful,
 * explicitly labeled horizon rather than pretending a static page can contain
 * an infinite schedule, and direct visitors to the actual live calendar.
 */
export function replaceBoundedCalendarContinuationWithLiveLink() {
  const control = document.querySelector(
    '[data-replica-capture-collection-control="next"]',
  );
  if (!control || control.getAttribute("data-hook") !== "daily-agenda-load-more-button") {
    return false;
  }
  const note = document.createElement("p");
  note.setAttribute("data-replica-live-calendar-note", "true");
  note.style.setProperty("display", "block", "important");
  note.style.setProperty("margin", "1rem 0", "important");
  note.textContent = "Current public events are captured in this static snapshot. ";
  const link = document.createElement("a");
  link.href = window.location.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("data-live-action", "true");
  link.textContent = "View the live Digital Equity calendar.";
  note.append(link);
  control.replaceWith(note);
  document.querySelectorAll("[data-replica-capture-collection-control]").forEach((element) => {
    element.removeAttribute("data-replica-capture-collection-control");
  });
  return true;
}


/**
 * Calendar images can open a Wix lightbox in #POPUPS_ROOT while the page is
 * hydrating. Mark only a visible popup and its explicit close control so the
 * capture loop can remove that temporary obstruction without touching page
 * content or unrelated overlays.
 */
export function markVisibleCalendarPopupDismissal() {
  const root = document.querySelector("#POPUPS_ROOT");
  if (!root) return { present: false, close_control: false };

  const isVisible = (element) => {
    if (!element?.isConnected || element.getClientRects().length === 0) return false;
    const style = getComputedStyle(element);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0"
    );
  };
  const popupSelector = [
    '[role="dialog"]',
    '[aria-modal="true"]',
    '[data-hook*="popup" i]',
    '[data-hook*="lightbox" i]',
    '[data-testid*="popup" i]',
    '[data-testid*="lightbox" i]',
  ].join(",");
  const popup = [
    ...root.querySelectorAll(popupSelector),
    ...root.children,
  ].find(isVisible);
  if (!popup) return { present: false, close_control: false };

  popup.setAttribute("data-replica-capture-popup-root", "calendar");
  const closeControl = [...popup.querySelectorAll(
    'button,[role="button"],[aria-label],[title],[data-hook],[data-testid]',
  )].find((candidate) => {
    if (!isVisible(candidate)) return false;
    const label = [
      candidate.getAttribute("aria-label"),
      candidate.getAttribute("title"),
      candidate.getAttribute("data-hook"),
      candidate.getAttribute("data-testid"),
      candidate.textContent,
    ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
    return /(?:^|\b)(?:close|dismiss|exit)(?:\b|$)/i.test(label);
  });
  if (!closeControl) return { present: true, close_control: false };

  closeControl.setAttribute("data-replica-capture-popup-dismiss", "calendar");
  return { present: true, close_control: true };
}


/** This function is serialized into Firefox by page.waitForFunction. */
export function markedCalendarPopupIsDismissed() {
  const popup = document.querySelector('[data-replica-capture-popup-root="calendar"]');
  if (!popup) return true;
  if (!popup.isConnected || popup.getClientRects().length === 0) return true;
  const style = getComputedStyle(popup);
  return style.display === "none" || style.visibility === "hidden" || style.opacity === "0";
}


/** This function is passed directly to page.evaluate. */
export function clickMarkedCalendarPopupDismissal() {
  const control = document.querySelector('[data-replica-capture-popup-dismiss="calendar"]');
  if (!control) return false;
  control.click();
  return true;
}


/** This function is passed directly to page.evaluate. */
export function clearCalendarPopupDismissalMarkers() {
  document.querySelectorAll(
    '[data-replica-capture-popup-root],[data-replica-capture-popup-dismiss]',
  ).forEach((element) => {
    element.removeAttribute("data-replica-capture-popup-root");
    element.removeAttribute("data-replica-capture-popup-dismiss");
  });
}


export async function dismissBlockingCalendarPopup(page) {
  const popup = await page.evaluate(markVisibleCalendarPopupDismissal);
  if (!popup.present) {
    return { detected: false, dismissed: false, method: null };
  }

  const waitForDismissal = () => page.waitForFunction(
    markedCalendarPopupIsDismissed,
    null,
    { timeout: CALENDAR_POPUP_DISMISSAL_TIMEOUT_MS },
  );
  try {
    if (popup.close_control && await page.evaluate(clickMarkedCalendarPopupDismissal)) {
      try {
        await waitForDismissal();
        return { detected: true, dismissed: true, method: "close-control" };
      } catch (_error) {
        // Some Wix lightboxes install their close listener after the image has
        // rendered. Escape is the documented fallback for that transient state.
      }
    }
    await page.keyboard.press("Escape");
    await waitForDismissal();
    return { detected: true, dismissed: true, method: "escape" };
  } catch (error) {
    throw new CaptureError(`could not dismiss visible Calendar popup in #POPUPS_ROOT (${error.message})`);
  } finally {
    await page.evaluate(clearCalendarPopupDismissalMarkers);
  }
}


export async function materializeProgressiveCollections(page) {
  const before = await page.evaluate(progressiveCollectionMetric);
  let clicks = 0;
  let retiredWithoutGrowth = 0;
  let calendarClicks = 0;
  let calendarPopupDismissals = 0;
  let calendarHorizonReached = false;
  for (; clicks < MAX_PROGRESSIVE_COLLECTION_EXPANSIONS;) {
    const control = await page.evaluate(markNextProgressiveCollectionControl);
    if (!control) break;
    if (
      control.hook === "daily-agenda-load-more-button" &&
      calendarClicks >= CALENDAR_STATIC_HORIZON_EXPANSIONS
    ) {
      calendarHorizonReached = true;
      break;
    }
    const beforeClick = await page.evaluate(progressiveCollectionMetric);
    const locator = page.locator(`[${CAPTURE_COLLECTION_ATTRIBUTE}="next"]`);
    try {
      if (control.hook === "daily-agenda-load-more-button") {
        const dismissal = await dismissBlockingCalendarPopup(page);
        if (dismissal.dismissed) calendarPopupDismissals += 1;
      }
      await locator.scrollIntoViewIfNeeded({ timeout: 10_000 });
      if (control.hook === "daily-agenda-load-more-button") {
        const dismissal = await dismissBlockingCalendarPopup(page);
        if (dismissal.dismissed) calendarPopupDismissals += 1;
      }
      await locator.click({ timeout: 10_000 });
      // A calendar button is briefly detached while Wix re-renders it after
      // every page. Detachment alone is a valid terminal signal for finite
      // galleries, but it must never make an open-ended public agenda look
      // exhausted before its next event rows have actually rendered.
      const transitionCheck = control.hook === "daily-agenda-load-more-button"
        ? progressiveCollectionHasAdvanced
        : progressiveCollectionTransitioned;
      await page.waitForFunction(
        transitionCheck,
        beforeClick,
        { timeout: 15_000 },
      );
      // Wix briefly fades and disables the same control while it appends the
      // next page. Re-scan only after that transition has settled; otherwise a
      // calendar capture can stop after one page despite more public rows.
      await page.waitForTimeout(800);
    } catch (error) {
      throw new CaptureError(
        `could not expand public ${control.label} collection content (${error.message})`,
      );
    }
    const afterClick = await page.evaluate(progressiveCollectionMetric);
    if (!collectionMetricAdvanced(beforeClick, afterClick)) retiredWithoutGrowth += 1;
    clicks += 1;
    if (control.hook === "daily-agenda-load-more-button") calendarClicks += 1;
  }
  const remaining = await page.evaluate(markNextProgressiveCollectionControl);
  let calendarContinuationRemoved = false;
  if (remaining && calendarHorizonReached && remaining.hook === "daily-agenda-load-more-button") {
    calendarContinuationRemoved = await page.evaluate(
      replaceBoundedCalendarContinuationWithLiveLink,
    );
    if (!calendarContinuationRemoved) {
      throw new CaptureError("could not replace the bounded live-calendar continuation");
    }
  } else if (remaining) {
    throw new CaptureError(
      `public ${remaining.label} collection still has more content after ${MAX_PROGRESSIVE_COLLECTION_EXPANSIONS} expansions`,
    );
  }
  const exhaustedControlsRemoved = await page.evaluate(
    removeExhaustedProgressiveCollectionControls,
  );
  const unresolved = await page.evaluate(markNextProgressiveCollectionControl);
  if (unresolved) {
    throw new CaptureError(`static capture left an active ${unresolved.label} collection control`);
  }
  const after = await page.evaluate(progressiveCollectionMetric);
  return {
    load_more_clicks: clicks,
    controls_retired_without_growth: retiredWithoutGrowth,
    exhausted_controls_removed: exhaustedControlsRemoved,
    calendar_horizon: calendarHorizonReached
      ? {
          clicks: calendarClicks,
          limit: CALENDAR_STATIC_HORIZON_EXPANSIONS,
          continuation_removed: calendarContinuationRemoved,
          policy: "volatile live agenda; continue on the live Digital Equity calendar",
        }
      : null,
    calendar_popup_dismissals: calendarPopupDismissals,
    before,
    after,
  };
}


/**
 * This function is passed directly to page.evaluate. Wix lazily mounts an
 * accordion response only after its header is activated, so mark the direct
 * widget headers before interacting with them one by one.
 */
export function discoverWixAccordionHeaders() {
  const root = document.querySelector('#PAGES_CONTAINER,main,[data-main-content="true" i]');
  if (!root) return [];
  const headers = [...root.querySelectorAll('button[data-hook="accordion-item-header"][aria-controls]')];
  return headers.map((header, index) => {
    const controls = (header.getAttribute("aria-controls") || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    header.setAttribute("data-replica-capture-disclosure", String(index));
    return {
      index,
      controls,
      label: (header.textContent || "").replace(/\s+/g, " ").trim(),
      initiallyExpanded: header.getAttribute("aria-expanded") === "true",
    };
  }).filter((header) => header.controls.length > 0 && header.label.length > 0);
}


/**
 * Read the live content after an accordion opens. Keeping this separate from
 * the final document matters because Wix closes and unmounts the previous
 * answer as soon as the next header is selected.
 */
export function readWixAccordionContent({ index, controls }) {
  const header = document.querySelector(
    `[data-replica-capture-disclosure="${index}"]`,
  );
  if (!header) return { ready: false, targets: [] };
  const targets = controls.map((id) => document.getElementById(id));
  const ready =
    header.getAttribute("aria-expanded") === "true" &&
    targets.every((target) => {
      if (!target) return false;
      const text = (target.textContent || "").replace(/\s+/g, "").trim();
      return Boolean(text || target.querySelector("img,video,audio,canvas,svg,table"));
    });
  return {
    ready,
    targets: targets.map((target, targetIndex) => ({
      id: controls[targetIndex],
      html: target ? target.innerHTML : "",
    })),
  };
}


/** This function is serialized into Firefox by page.waitForFunction. */
export function wixAccordionContentIsReady({ index, controls }) {
  const header = document.querySelector(
    `[data-replica-capture-disclosure="${index}"]`,
  );
  if (!header || header.getAttribute("aria-expanded") !== "true") return false;
  return controls.every((id) => {
    const target = document.getElementById(id);
    if (!target) return false;
    const text = (target.textContent || "").replace(/\s+/g, "").trim();
    return Boolean(text || target.querySelector("img,video,audio,canvas,svg,table"));
  });
}


export async function materializeWixAccordionContent(page) {
  const headers = await page.evaluate(discoverWixAccordionHeaders);
  const records = [];
  for (const header of headers) {
    const locator = page.locator(
      `[${CAPTURE_DISCLOSURE_ATTRIBUTE}="${header.index}"]`,
    );
    try {
      if (!header.initiallyExpanded) {
        await locator.scrollIntoViewIfNeeded({ timeout: 10_000 });
        await locator.click({ timeout: 10_000 });
      }
      await page.waitForFunction(
        wixAccordionContentIsReady,
        { index: header.index, controls: header.controls },
        { timeout: 10_000 },
      );
      const content = await page.evaluate(readWixAccordionContent, {
        index: header.index,
        controls: header.controls,
      });
      if (!content.ready) {
        throw new CaptureError("the opened response did not contain public content");
      }
      records.push({ ...header, targets: content.targets });
    } catch (error) {
      const label = header.label.slice(0, 120) || `accordion ${header.index + 1}`;
      throw new CaptureError(`could not materialize Wix accordion response: ${label} (${error.message})`);
    }
  }
  return records;
}


/**
 * Replace only direct Wix accordion items with native details/summary markup.
 * Native details preserve readable source text after scripts are removed, while
 * still allowing a visitor to collapse an answer voluntarily.
 */
export function replaceWixAccordionsWithNativeDisclosures(records) {
  const result = { disclosures: 0, missing: [] };
  for (const record of records) {
    const header = document.querySelector(
      `[data-replica-capture-disclosure="${record.index}"]`,
    );
    const item = header?.parentElement;
    if (!header || !item || !record.targets?.length) {
      result.missing.push(record.index);
      continue;
    }
    const controlsRemainInsideItem = record.controls.every((id) => {
      const target = document.getElementById(id);
      return target && item.contains(target);
    });
    if (!controlsRemainInsideItem) {
      result.missing.push(record.index);
      continue;
    }

    const details = document.createElement("details");
    details.open = true;
    details.setAttribute("data-replica-static-disclosure", "true");
    // Keep the header's horizontal navigation rhythm.  The previous block
    // override made each recovered menu wrap onto a new line in the static
    // header, even while closed.
    details.style.setProperty("display", "inline-block", "important");
    details.style.setProperty("width", "100%", "important");
    details.style.setProperty("height", "auto", "important");
    details.style.setProperty("overflow", "visible", "important");

    const summary = document.createElement("summary");
    summary.setAttribute("data-replica-static-summary", "true");
    summary.textContent = record.label;
    summary.style.setProperty("cursor", "pointer", "important");
    summary.style.setProperty("padding", "0.75rem 0", "important");
    summary.style.setProperty("font", "inherit", "important");

    const content = document.createElement("div");
    content.setAttribute("data-replica-static-content", "true");
    content.style.setProperty("display", "block", "important");
    content.style.setProperty("height", "auto", "important");
    content.style.setProperty("max-height", "none", "important");
    content.style.setProperty("overflow", "visible", "important");
    content.style.setProperty("padding", "0 0 1rem", "important");
    for (const target of record.targets) {
      const section = document.createElement("div");
      section.setAttribute("data-replica-static-content-source", target.id);
      section.innerHTML = target.html;
      content.append(section);
    }
    details.append(summary, content);
    item.replaceWith(details);
    result.disclosures += 1;
  }
  document.querySelectorAll("[data-replica-capture-disclosure]").forEach((element) => {
    element.removeAttribute("data-replica-capture-disclosure");
  });
  return result;
}


/**
 * The Wix header already contains its submenu links, but hides them behind a
 * JavaScript-only button. Convert that direct, public link list to native
 * details markup so the static replica still exposes its routes.
 */
export function replaceWixNavigationMenusWithNativeDisclosures() {
  let converted = 0;
  for (const item of [...document.querySelectorAll("nav li")]) {
    if (!item.isConnected) continue;
    const children = [...item.children];
    const submenu = children.find((child) =>
      child.localName === "ul" && child.querySelector(":scope > li > a[href]"),
    );
    const trigger = children.find((child) => child.getAttribute("aria-haspopup") === "true");
    const label = (trigger?.textContent || "").replace(/\s+/g, " ").trim();
    if (!submenu || !trigger || !label) continue;

    const details = document.createElement("details");
    details.setAttribute("data-replica-static-menu", "true");
    details.style.setProperty("display", "block", "important");
    details.style.setProperty("position", "static", "important");
    details.style.setProperty("visibility", "visible", "important");
    details.style.setProperty("height", "auto", "important");
    details.style.setProperty("overflow", "visible", "important");

    const summary = document.createElement("summary");
    summary.textContent = label;
    summary.setAttribute("data-replica-static-menu-summary", "true");
    summary.style.setProperty("cursor", "pointer", "important");
    summary.style.setProperty("font", "inherit", "important");

    // Preserve the public links, not Wix's generated hidden-menu state. A
    // cloned, plain list can be opened natively without inheriting a
    // display:none or aria-hidden state from the original widget.
    const staticList = document.createElement("ul");
    staticList.setAttribute("data-replica-static-menu-content", "true");
    staticList.style.setProperty("display", "block", "important");
    staticList.style.setProperty("position", "static", "important");
    staticList.style.setProperty("visibility", "visible", "important");
    staticList.style.setProperty("height", "auto", "important");
    staticList.style.setProperty("max-height", "none", "important");
    staticList.style.setProperty("overflow", "visible", "important");
    for (const child of [...submenu.children]) {
      staticList.append(child.cloneNode(true));
    }
    staticList.querySelectorAll("a[tabindex='-1']").forEach((anchor) => {
      anchor.removeAttribute("tabindex");
    });

    details.append(summary, staticList);
    item.replaceChildren(details);
    item.setAttribute("data-replica-static-menu-item", "true");
    item.style.setProperty("display", "inline-block", "important");
    item.style.setProperty("position", "static", "important");
    item.style.setProperty("visibility", "visible", "important");
    item.style.setProperty("width", "auto", "important");
    item.style.setProperty("height", "auto", "important");
    item.style.setProperty("overflow", "visible", "important");
    converted += 1;
  }
  return converted;
}


/**
 * This function is passed directly to page.evaluate, so it must remain
 * self-contained and use browser globals only.
 */
export function sanitizeDocument() {
  const removeSelectors = [
    "script",
    "template",
    "noscript",
    "object",
    "embed",
    'meta[http-equiv="refresh" i]',
    'link[rel~="preload" i]',
    'link[rel~="prefetch" i]',
    'link[rel~="modulepreload" i]',
    'link[rel~="prerender" i]',
    'link[rel~="dns-prefetch" i]',
    'link[rel~="preconnect" i]',
  ];
  document.querySelectorAll(removeSelectors.join(",")).forEach((element) => element.remove());
  document.querySelectorAll("style").forEach((style) => {
    if (style.textContent.includes("--cookie-banner-")) style.remove();
  });

  document.querySelectorAll("img").forEach((image) => {
    if (image.currentSrc) image.setAttribute("src", image.currentSrc);
    image.removeAttribute("srcset");
    image.removeAttribute("sizes");
  });

  const navigationAttributes = new Set(["href", "action", "formaction", "xlink:href"]);
  const sensitiveAttribute = /(?:x-)?xsrf|csrf|(?:session|access)[-_:]?(?:token|key)/i;
  const sensitiveValue = /wix-(?:essential-)?viewer-model|(?:x-)?xsrf-token|["'](?:sessionToken|accessToken)["']\s*[:=]/i;
  document.querySelectorAll("*").forEach((element) => {
    let removeElement = false;
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (name.startsWith("on") || name === "srcdoc") {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (navigationAttributes.has(name) && /^(?:javascript|vbscript|data)\s*:/i.test(value)) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (name === "src" && /^(?:javascript|vbscript)\s*:/i.test(value)) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (sensitiveAttribute.test(name) || sensitiveValue.test(value)) {
        removeElement = true;
        break;
      }
    }
    if (removeElement) element.remove();
  });

  // Wix keeps submission, error, and "submit another" screens mounted in
  // the initial document, then merely hides them until the form state changes.
  // They are not public instructional copy, and publishing them in a static
  // snapshot makes a never-submitted form look broken or already complete.
  const normalizedText = (element) => (element.textContent || "").replace(/\s+/g, " ").trim();
  const isTransientFormState = (text) =>
    /^(?:an error occurred\. try again later|your content has been submitted|thanks for reaching out!|submit another(?: [^.!]{1,96})?!|widget didn[’']t load)$/i.test(text);
  document.querySelectorAll("[aria-live],[role='alert'],[data-testid='richTextElement']").forEach((element) => {
    const text = normalizedText(element);
    const isEmptyLiveRegion =
      !text &&
      (element.matches("[aria-live]") || element.matches("[role='alert']"));
    if (isEmptyLiveRegion || isTransientFormState(text)) element.remove();
  });
  // Wix error widgets are not consistently marked as alerts or rich text.
  // Remove only a leaf whose complete visible text is a known transient state,
  // never a container with public page content.
  document.querySelectorAll("*").forEach((element) => {
    if (!element.isConnected || !isTransientFormState(normalizedText(element))) return;
    const childRepeatsState = [...element.children].some((child) =>
      isTransientFormState(normalizedText(child)),
    );
    if (!childRepeatsState) element.remove();
  });

  // A static replica must never present an interactive-looking booking,
  // registration, upload, or submit affordance that cannot work. Keep the
  // original public action available by replacing visible action controls with
  // a direct link to the approved source page that supplied the snapshot.
  const liveSourceHref = window.location.href;
  const actionLabelPattern = /^(?:register(?:\s+here)?|book(?:\s+now)?|submit|send|share|subscribe|view|see|read|upload(?:\s+(?:a\s+)?(?:file|photo|design|resume))?|apply|sign\s*up|reserve|continue|join(?:\s+now)?)\b/i;
  const makeLiveActionLink = (label) => {
    const link = document.createElement("a");
    link.href = liveSourceHref;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("data-replica-live-action", "true");
    link.textContent = label
      ? `${label} on the Digital Equity site`
      : "Continue on the Digital Equity site";
    link.setAttribute(
      "aria-label",
      label
        ? `${label} on the live Digital Equity page`
        : "Continue on the live Digital Equity page",
    );
    return link;
  };
  document.querySelectorAll("button,input[type='button' i],input[type='submit' i],input[type='reset' i],[role='button']").forEach((control) => {
    if (!control.isConnected || control.matches("a[href]")) return;
    // Wix can temporarily report a collapsed client rect for an otherwise
    // published control while a page is hydrating. Semantic hidden state is
    // reliable; geometry and opacity are not.
    if (control.closest("[hidden],[aria-hidden='true']")) return;
    const label = (
      control.getAttribute("aria-label") ||
      control.getAttribute("value") ||
      control.innerText ||
      control.textContent ||
      ""
    ).replace(/\s+/g, " ").trim();
    if (!actionLabelPattern.test(label)) return;
    const link = makeLiveActionLink(label);
    for (const name of ["id", "class", "style"]) {
      if (control.hasAttribute(name)) link.setAttribute(name, control.getAttribute(name));
    }
    control.replaceWith(link);
  });

  document.querySelectorAll("form").forEach((form) => {
    const shell = document.createElement("div");
    for (const attribute of [...form.attributes]) {
      if (!["action", "method", "enctype", "target", "autocomplete", "name"].includes(attribute.name.toLowerCase())) {
        shell.setAttribute(attribute.name, attribute.value);
      }
    }
    shell.setAttribute("role", "group");
    shell.setAttribute("aria-disabled", "true");
    shell.setAttribute("data-replica-inert", "form");
    shell.replaceChildren(...form.childNodes);
    if (!shell.querySelector("[data-replica-live-action]")) {
      shell.append(" ", makeLiveActionLink("Use this form"));
    }
    form.replaceWith(shell);
  });
  // A static mirror must not look like an application with broken fields,
  // filters, pagination, or social controls. Public text remains in place;
  // actions were converted above to direct official-source links. A few Wix
  // panels put their only human-readable title inside a disabled button, so
  // retain substantial non-action labels as plain static text before removing
  // the control itself.
  const disposableControlLabel = /^(?:skip to (?:main )?content|all locations|like|more(?: actions)?|previous|next|play(?: video)?|expand image|show more|load more|close|menu|filter|choose|clear|reset)$/i;
  document.querySelectorAll("button").forEach((control) => {
    const label = (
      control.getAttribute("aria-label") || control.innerText || control.textContent || ""
    ).replace(/\s+/g, " ").trim();
    if (!label || disposableControlLabel.test(label)) {
      control.remove();
      return;
    }
    const staticLabel = document.createElement("p");
    staticLabel.setAttribute("data-replica-static-control-label", "true");
    staticLabel.textContent = label;
    control.replaceWith(staticLabel);
  });
  document.querySelectorAll("input,textarea,select").forEach((control) => control.remove());
  document.querySelectorAll('[role="button" i]').forEach((button) => {
    if (button.matches("a[href]")) return;
    // Some Wix media cards use a button role for their container. Keep their
    // public image/text but turn the container into ordinary static markup.
    button.removeAttribute("role");
    button.removeAttribute("inert");
    button.removeAttribute("aria-disabled");
    button.removeAttribute("tabindex");
    button.removeAttribute("data-replica-inert");
  });

  document.querySelectorAll("iframe").forEach((frame) => {
    frame.removeAttribute("srcdoc");
    const rawSource = frame.getAttribute("src");
    const previewSource = frame.getAttribute("data-replica-preview") || "";
    let source;
    if (rawSource) {
      try {
        source = new URL(rawSource, document.baseURI);
      } catch (_error) {
        source = null;
      }
    }

    const placeholder = document.createElement("div");
    placeholder.setAttribute("data-replica-embed-placeholder", "true");
    placeholder.setAttribute("role", "group");
    placeholder.setAttribute("aria-label", frame.title || "Embedded content");
    for (const attribute of ["class", "style", "width", "height"]) {
      if (frame.hasAttribute(attribute)) {
        placeholder.setAttribute(attribute, frame.getAttribute(attribute));
      }
    }
    if (source && (source.protocol === "https:" || source.protocol === "http:")) {
      const link = document.createElement("a");
      link.href = source.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const label = frame.title
        ? `Open embedded content: ${frame.title}`
        : `Open embedded content from ${source.hostname}`;
      const interactiveEmbed = /\b(?:forms?|subscribe|newsletter|maps?|calendar|scheduler|booking|register)\b/i.test(
        `${frame.title || ""} ${source.hostname}`,
      );
      if (!interactiveEmbed && /^data:image\/png;base64,[a-z0-9+/]+=*$/i.test(previewSource)) {
        const preview = document.createElement("img");
        preview.src = previewSource;
        preview.alt = `${label} (static preview)`;
        preview.setAttribute("data-replica-embed-preview", "true");
        preview.style.cssText = "display:block;width:100%;height:100%;object-fit:cover";
        link.setAttribute("aria-label", label);
        link.style.cssText = "display:block;width:100%;height:100%";
        link.append(preview);
        placeholder.setAttribute("data-replica-static-preview", "true");
      } else {
        link.textContent = label;
      }
      placeholder.append(link);
      const note = document.createElement("p");
      note.setAttribute("data-replica-static-preview-note", "true");
      note.textContent = interactiveEmbed
        ? "Interactive content is available on the live Digital Equity site."
        : "Static preview — use the link for the live content.";
      placeholder.append(note);
    } else {
      placeholder.textContent = frame.title || "Embedded content is available on the original page.";
    }
    frame.replaceWith(placeholder);
  });

  document.querySelectorAll("a[target=\"_blank\"]").forEach((anchor) => {
    const values = new Set((anchor.getAttribute("rel") || "").split(/\s+/).filter(Boolean));
    values.add("noopener");
    values.add("noreferrer");
    anchor.setAttribute("rel", [...values].join(" "));
  });

  document.querySelectorAll('meta[name="robots" i]').forEach((meta) => meta.remove());
  document.querySelectorAll('meta[name="referrer" i]').forEach((meta) => meta.remove());
  const noindex = document.createElement("meta");
  noindex.setAttribute("name", "robots");
  noindex.setAttribute("content", "noindex,nofollow,noarchive");
  const noReferrer = document.createElement("meta");
  noReferrer.setAttribute("name", "referrer");
  noReferrer.setAttribute("content", "no-referrer");
  const head = document.head || document.documentElement;
  head.prepend(noReferrer, noindex);
  if (document.head) {
    [...document.head.childNodes]
      .filter((node) => node.nodeType === Node.TEXT_NODE && !node.textContent.trim())
      .forEach((node) => node.remove());
  }
  document.documentElement.setAttribute("data-replica-snapshot", "true");
}


/** This function also runs in the browser after sanitizeDocument. */
export function auditSanitizedDocument() {
  const issues = [];
  const add = (message) => {
    if (issues.length < 50) issues.push(message);
  };
  if (document.querySelector("script,template,noscript,object,embed,iframe,form")) {
    add("executable or hidden element remains");
  }
  if (document.querySelector('meta[http-equiv="refresh" i]')) add("meta refresh remains");
  if (document.querySelector('link[rel~="preload" i],link[rel~="prefetch" i],link[rel~="modulepreload" i],link[rel~="prerender" i],link[rel~="dns-prefetch" i],link[rel~="preconnect" i]')) {
    add("transient resource hint remains");
  }
  document.querySelectorAll("*").forEach((element) => {
    for (const attribute of element.attributes) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (name.startsWith("on")) add(`inline handler remains on ${element.localName}`);
      if (name === "srcdoc") add("iframe srcdoc remains");
      if (["href", "action", "formaction", "xlink:href"].includes(name) && /^(?:javascript|vbscript|data)\s*:/i.test(value)) {
        add(`executable ${name} remains on ${element.localName}`);
      }
      if (name === "src" && /^(?:javascript|vbscript)\s*:/i.test(value)) {
        add(`executable src remains on ${element.localName}`);
      }
    }
  });
  if (document.querySelector("button,input,textarea,select")) add("interactive control remains");
  document.querySelectorAll('[role="button" i]:not(a[href])').forEach((button) => {
    add("button role remains");
  });
  if (document.querySelector("img[srcset],img[sizes]")) add("responsive image candidates remain");
  if (document.querySelectorAll('meta[name="robots" i][content*="noindex" i]').length !== 1) {
    add("noindex directive is missing or duplicated");
  }
  if (document.querySelectorAll('meta[name="referrer" i][content="no-referrer" i]').length !== 1) {
    add("no-referrer directive is missing or duplicated");
  }
  if (document.documentElement.outerHTML.match(/wix-(?:essential-)?viewer-model|(?:x-)?xsrf-token|["'](?:sessionToken|accessToken)["']\s*[:=]|--cookie-banner-/i)) {
    add("viewer, session, or transient cookie data remains");
  }
  return issues;
}


async function hydratePage(page) {
  await page.locator("body").waitFor({ state: "attached" });
  await page.locator('#PAGES_CONTAINER,main,[data-main-content="true" i]').first().waitFor({
    state: "attached",
    timeout: 30_000,
  });
  await page.evaluate(() => {
    document.querySelectorAll("img").forEach((image) => {
      image.loading = "eager";
    });
  });

  try {
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
  } catch (_error) {
    // Wix telemetry can keep a connection active; scrolling below remains bounded.
  }

  let previousHeight = -1;
  let stablePasses = 0;
  for (let step = 0; step < 120 && stablePasses < 3; step += 1) {
    const metrics = await page.evaluate(() => ({
      height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
      y: window.scrollY,
      viewport: window.innerHeight,
    }));
    const nextY = Math.min(metrics.height, metrics.y + Math.max(400, Math.floor(metrics.viewport * 0.75)));
    await page.evaluate((y) => window.scrollTo(0, y), nextY);
    await page.waitForTimeout(120);
    if (nextY >= metrics.height - metrics.viewport && metrics.height === previousHeight) {
      stablePasses += 1;
    } else {
      stablePasses = 0;
    }
    previousHeight = metrics.height;
  }

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(500);
  try {
    await page.waitForFunction(
      () => [...document.images].every((image) => !image.currentSrc || image.complete),
      null,
      { timeout: 10_000 },
    );
  } catch (_error) {
    // Broken remote media remains represented by its resolved currentSrc.
  }
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
    window.scrollTo(0, 0);
  });
  await page.evaluate(
    ({ quietMilliseconds, maximumMilliseconds }) => new Promise((resolve) => {
      let quietTimer;
      let maximumTimer;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        clearTimeout(quietTimer);
        clearTimeout(maximumTimer);
        resolve();
      };
      const restartQuietWindow = () => {
        clearTimeout(quietTimer);
        quietTimer = setTimeout(finish, quietMilliseconds);
      };
      const observer = new MutationObserver(restartQuietWindow);
      observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["src", "srcset"],
      });
      maximumTimer = setTimeout(finish, maximumMilliseconds);
      restartQuietWindow();
    }),
    { quietMilliseconds: 1_500, maximumMilliseconds: 10_000 },
  );
}


async function captureIframePreviews(page) {
  const frames = page.locator("iframe");
  const count = await frames.count();
  const previews = new Array(count).fill("");
  for (let index = 0; index < count; index += 1) {
    const frame = frames.nth(index);
    const box = await frame.boundingBox();
    if (!box || box.width < 2 || box.height < 2 || box.width * box.height > 8_000_000) continue;
    try {
      const png = await frame.screenshot({
        animations: "disabled",
        caret: "hide",
        timeout: 15_000,
      });
      previews[index] = `data:image/png;base64,${png.toString("base64")}`;
    } catch (_error) {
      // The outbound link remains when a public embed cannot be rendered safely.
    }
  }
  await page.evaluate((values) => {
    document.querySelectorAll("iframe").forEach((frame, index) => {
      if (values[index]) frame.setAttribute("data-replica-preview", values[index]);
    });
    window.scrollTo(0, 0);
  }, previews);
  return previews.filter(Boolean).length;
}


function expectedStatus(route, allowedStatuses) {
  return allowedStatuses.get(route.url) ?? 200;
}


/**
 * A full capture is atomic, so one short-lived DNS or transport failure must
 * not discard a complete public-source run. Status/content failures are never
 * retried here: only errors before the official page can be reached are.
 */
export function isTransientNavigationFailure(error) {
  const message = String(error?.message || error || "");
  return /(?:NS_ERROR_UNKNOWN_HOST|NS_ERROR_NET_RESET|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_(?:RESET|CLOSED)|ERR_NETWORK_CHANGED|ERR_TIMED_OUT|navigation timeout|timeout \d+ms exceeded|did not return a main-frame response)/i.test(message);
}


async function navigateToOfficialPage(page, route, navigationTimeoutMs) {
  let lastError = null;
  for (let attempt = 1; attempt <= MAX_TRANSIENT_NAVIGATION_ATTEMPTS; attempt += 1) {
    try {
      const response = await page.goto(route.url, {
        waitUntil: "domcontentloaded",
        timeout: navigationTimeoutMs,
      });
      if (!response) {
        throw new CaptureError(`${route.url} did not return a main-frame response`);
      }
      return response;
    } catch (error) {
      lastError = error;
      if (!isTransientNavigationFailure(error) || attempt === MAX_TRANSIENT_NAVIGATION_ATTEMPTS) {
        throw error;
      }
      await page.waitForTimeout(750 * attempt);
    }
  }
  throw lastError;
}


async function captureRoute(browser, route, snapshotDirectory, options) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    userAgent: FIXED_USER_AGENT,
    locale: "en-US",
    timezoneId: "America/New_York",
    colorScheme: "light",
    reducedMotion: "reduce",
    serviceWorkers: "block",
    storageState: { cookies: [], origins: [] },
    extraHTTPHeaders: { "Accept-Language": "en-US,en;q=0.9" },
  });

  try {
    const page = await context.newPage();
    page.setDefaultNavigationTimeout(options.navigationTimeoutMs);
    const response = await navigateToOfficialPage(page, route, options.navigationTimeoutMs);

    const status = response.status();
    const permittedStatus = expectedStatus(route, options.allowedStatuses);
    if (status !== permittedStatus) {
      throw new CaptureError(`${route.url} returned ${status}; expected ${permittedStatus}`);
    }
    const finalUrl = canonicalSourceUrl(page.url());
    if (finalUrl !== route.url) {
      throw new CaptureError(`${route.url} redirected to a different indexed route: ${page.url()}`);
    }

    await hydratePage(page);
    const progressiveCollections = await materializeProgressiveCollections(page);
    if (progressiveCollections.load_more_clicks > 0) await hydratePage(page);
    const accordionRecords = await materializeWixAccordionContent(page);
    // Opening one Wix accordion closes the previous one. Let the live page
    // settle once more, then replace every recorded response at once below.
    if (accordionRecords.length > 0) await hydratePage(page);
    const staticContent = await page.evaluate(
      replaceWixAccordionsWithNativeDisclosures,
      accordionRecords,
    );
    if (staticContent.missing.length > 0 || staticContent.disclosures !== accordionRecords.length) {
      throw new CaptureError(
        `${route.url} could not replace every materialized Wix accordion (${staticContent.missing.join(", ")})`,
      );
    }
    const staticNavigationMenus = await page.evaluate(
      replaceWixNavigationMenusWithNativeDisclosures,
    );
    const embedPreviews = await captureIframePreviews(page);
    const capturedCookies = await context.cookies();
    if (capturedCookies.length > 0) {
      throw new CaptureError(`${route.url} stored cookies despite the no-cookie Firefox policy`);
    }
    const metadata = await page.evaluate(() => ({
      title: document.title.trim(),
      siteRevision:
        document.querySelector('meta[http-equiv="X-Wix-Published-Version" i]')?.getAttribute("content")?.trim() || null,
    }));
    if (!metadata.title) throw new CaptureError(`${route.url} has an empty document title`);
    if (!metadata.siteRevision || !/^\d+$/.test(metadata.siteRevision)) {
      throw new CaptureError(`${route.url} is missing a numeric Wix published revision`);
    }
    const siteRevision = Number(metadata.siteRevision);
    if (!Number.isSafeInteger(siteRevision)) {
      throw new CaptureError(`${route.url} has an unsupported Wix published revision`);
    }

    await page.evaluate(sanitizeDocument);
    const sanitizationIssues = await page.evaluate(auditSanitizedDocument);
    if (sanitizationIssues.length > 0) {
      throw new CaptureError(`${route.url} failed sanitization: ${sanitizationIssues.join("; ")}`);
    }
    const html = await page.content();
    assertSanitized(html);
    assertStaticContentMaterialized(html, {
      disclosures: staticContent.disclosures,
      navigationMenus: staticNavigationMenus,
    });
    const source = Buffer.from(html, "utf8");
    const snapshot = await deterministicGzip(source);
    const file = `replica-snapshots/${route.id}.html.gz`;
    await writeFile(path.join(snapshotDirectory, `${route.id}.html.gz`), snapshot, { flag: "wx" });
    const responseHeaders = await response.allHeaders();

    return Object.freeze({
      id: route.id,
      url: route.url,
      final_url: finalUrl,
      path: route.path,
      file,
      status,
      title: metadata.title,
      site_revision: siteRevision,
      etag: responseHeaders.etag || null,
      embed_previews: embedPreviews,
      static_content: {
        wix_accordions: staticContent.disclosures,
        navigation_menus: staticNavigationMenus,
        progressive_collections: progressiveCollections,
      },
      source_bytes: source.byteLength,
      snapshot_bytes: snapshot.byteLength,
      source_sha256: sha256(source),
      snapshot_sha256: sha256(snapshot),
    });
  } finally {
    await context.close();
  }
}


async function captureRoutes(browser, routes, snapshotDirectory, options) {
  const results = new Array(routes.length);
  let cursor = 0;
  let failure = null;
  const workerCount = Math.min(options.concurrency, routes.length);
  const workers = Array.from({ length: workerCount }, async () => {
    while (!failure) {
      const index = cursor;
      cursor += 1;
      if (index >= routes.length) return;
      const route = routes[index];
      try {
        results[index] = await captureRoute(browser, route, snapshotDirectory, options);
        process.stderr.write(`[${index + 1}/${routes.length}] captured ${route.path}\n`);
      } catch (error) {
        failure = error;
      }
    }
  });
  await Promise.all(workers);
  if (failure) throw failure;
  if (results.some((result) => !result)) {
    throw new CaptureError("capture ended without an artifact for every selected route");
  }
  return results;
}


export function validateCapturedPages(routes, pages) {
  if (pages.length !== routes.length) {
    throw new CaptureError(`captured ${pages.length} pages for ${routes.length} selected routes`);
  }
  const files = new Set();
  for (let index = 0; index < routes.length; index += 1) {
    const route = routes[index];
    const page = pages[index];
    if (page.id !== route.id || page.url !== route.url || page.path !== route.path) {
      throw new CaptureError(`captured route ${index + 1} does not match site-index.json`);
    }
    if (files.has(page.file)) throw new CaptureError(`duplicate snapshot file: ${page.file}`);
    files.add(page.file);
  }
  const revisions = new Set(pages.map((page) => page.site_revision));
  if (revisions.size !== 1 || !Number.isSafeInteger([...revisions][0])) {
    throw new CaptureError(`capture contains mixed or missing Wix revisions: ${[...revisions].join(", ")}`);
  }
  return [...revisions][0];
}


async function pathExists(filePath) {
  try {
    await access(filePath, fsConstants.F_OK);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}


function publicationPaths(outputRoot, suffix) {
  return {
    targetSnapshots: path.join(outputRoot, "replica-snapshots"),
    targetManifest: path.join(outputRoot, "replica-manifest.json"),
    backupSnapshots: path.join(outputRoot, `.replica-snapshots.previous-${suffix}`),
    backupManifest: path.join(outputRoot, `.replica-manifest.previous-${suffix}.json`),
    transaction: path.join(outputRoot, `.replica-publish-${suffix}.json`),
  };
}


async function writeDurableJson(filePath, value) {
  const handle = await open(filePath, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
}


async function recoverPublication(outputRoot, transactionPath) {
  let state;
  try {
    state = JSON.parse(await readFile(transactionPath, "utf8"));
  } catch (error) {
    throw new CaptureError(`cannot recover interrupted publication ${transactionPath}: ${error.message}`);
  }
  if (
    !state ||
    typeof state !== "object" ||
    !/^[0-9]+-[a-f0-9]{10}$/.test(state.suffix || "") ||
    typeof state.old_snapshots !== "boolean" ||
    typeof state.old_manifest !== "boolean"
  ) {
    throw new CaptureError(`interrupted publication has invalid recovery data: ${transactionPath}`);
  }
  const paths = publicationPaths(outputRoot, state.suffix);
  const present = {
    targetSnapshots: await pathExists(paths.targetSnapshots),
    targetManifest: await pathExists(paths.targetManifest),
    backupSnapshots: await pathExists(paths.backupSnapshots),
    backupManifest: await pathExists(paths.backupManifest),
  };
  const complete =
    present.targetSnapshots &&
    present.targetManifest &&
    (!state.old_snapshots || present.backupSnapshots) &&
    (!state.old_manifest || present.backupManifest);

  if (!complete) {
    if (state.old_snapshots && present.backupSnapshots) {
      if (present.targetSnapshots) await rm(paths.targetSnapshots, { recursive: true, force: true });
      await rename(paths.backupSnapshots, paths.targetSnapshots);
    } else if (!state.old_snapshots && present.targetSnapshots) {
      await rm(paths.targetSnapshots, { recursive: true, force: true });
    }
    if (state.old_manifest && present.backupManifest) {
      if (present.targetManifest) await rm(paths.targetManifest, { force: true });
      await rename(paths.backupManifest, paths.targetManifest);
    } else if (!state.old_manifest && present.targetManifest) {
      await rm(paths.targetManifest, { force: true });
    }
  }

  await rm(paths.backupSnapshots, { recursive: true, force: true });
  await rm(paths.backupManifest, { force: true });
  await rm(transactionPath, { force: true });
}


export async function recoverInterruptedPublications(outputRoot) {
  const entries = await readdir(outputRoot, { withFileTypes: true });
  const transactions = entries
    .filter((entry) => entry.isFile() && /^\.replica-publish-[0-9]+-[a-f0-9]{10}\.json$/.test(entry.name))
    .map((entry) => path.join(outputRoot, entry.name))
    .sort();
  for (const transaction of transactions) {
    await recoverPublication(outputRoot, transaction);
  }
}


export async function atomicPublish(stagingRoot, outputRoot) {
  const sourceSnapshots = path.join(stagingRoot, "replica-snapshots");
  const sourceManifest = path.join(stagingRoot, "replica-manifest.json");
  const suffix = `${process.pid}-${randomBytes(5).toString("hex")}`;
  const paths = publicationPaths(outputRoot, suffix);
  const state = {
    suffix,
    old_snapshots: await pathExists(paths.targetSnapshots),
    old_manifest: await pathExists(paths.targetManifest),
  };
  await writeDurableJson(paths.transaction, state);

  try {
    if (state.old_snapshots) {
      await rename(paths.targetSnapshots, paths.backupSnapshots);
    }
    if (state.old_manifest) {
      await rename(paths.targetManifest, paths.backupManifest);
    }
    await rename(sourceSnapshots, paths.targetSnapshots);
    await rename(sourceManifest, paths.targetManifest);
  } catch (error) {
    await recoverPublication(outputRoot, paths.transaction);
    throw error;
  }

  await recoverPublication(outputRoot, paths.transaction);
}


async function acquireCaptureLock(outputRoot) {
  const lockPath = path.join(outputRoot, ".replica-capture.lock");
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const handle = await open(lockPath, "wx", 0o600);
      const value = {
        pid: process.pid,
        host: hostname(),
        started_at: new Date().toISOString(),
      };
      await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8");
      await handle.sync();
      return async () => {
        await handle.close();
        await rm(lockPath, { force: true });
      };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      let owner;
      try {
        owner = JSON.parse(await readFile(lockPath, "utf8"));
      } catch (readError) {
        throw new CaptureError(`capture lock exists and cannot be read: ${readError.message}`);
      }
      let active = owner.host !== hostname() || !Number.isSafeInteger(owner.pid);
      if (!active) {
        try {
          process.kill(owner.pid, 0);
          active = true;
        } catch (processError) {
          if (processError.code !== "ESRCH") active = true;
        }
      }
      if (active || attempt === 1) {
        throw new CaptureError(`another capture owns ${lockPath}`);
      }
      await rm(lockPath, { force: true });
    }
  }
  throw new CaptureError(`could not acquire capture lock in ${outputRoot}`);
}


export function buildManifest({ timestamp, browserVersion, pages }) {
  return {
    captured_at: timestamp,
    source_origin: SOURCE_ORIGIN,
    route_count: pages.length,
    capture: {
      browser: { name: "firefox", version: browserVersion },
      viewport: VIEWPORT,
    },
    pages,
  };
}


export async function run(options) {
  const indexDocument = JSON.parse(await readFile(options.indexPath, "utf8"));
  const indexedRoutes = validateIndex(indexDocument);
  const { selected, partial } = selectRoutes(indexedRoutes, options);
  await mkdir(options.outputDir, { recursive: true });
  const releaseLock = await acquireCaptureLock(options.outputDir);
  let stagingRoot;

  let browser;
  try {
    await recoverInterruptedPublications(options.outputDir);
    stagingRoot = await mkdtemp(path.join(options.outputDir, ".replica-capture-"));
    const snapshotDirectory = path.join(stagingRoot, "replica-snapshots");
    await mkdir(snapshotDirectory);
    browser = await firefox.launch({
      headless: true,
      firefoxUserPrefs: {
        "network.cookie.cookieBehavior": 2,
        "network.cookie.cookieBehavior.pbmode": 2,
      },
    });
    const pages = await captureRoutes(browser, selected, snapshotDirectory, options);
    validateCapturedPages(selected, pages);
    const manifest = buildManifest({
      timestamp: capturedAt(),
      browserVersion: browser.version(),
      pages,
    });
    await writeFile(
      path.join(stagingRoot, "replica-manifest.json"),
      `${JSON.stringify(manifest, null, 2)}\n`,
      { encoding: "utf8", flag: "wx" },
    );
    await atomicPublish(stagingRoot, options.outputDir);
    return { manifest, partial };
  } finally {
    if (browser) await browser.close();
    if (stagingRoot) await rm(stagingRoot, { recursive: true, force: true });
    await releaseLock();
  }
}


async function main() {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      process.stdout.write(helpText());
      return;
    }
    const { manifest, partial } = await run(options);
    process.stdout.write(
      `${partial ? "Smoke capture" : "Full capture"} published ${manifest.route_count} route${manifest.route_count === 1 ? "" : "s"} ` +
      `at Wix revision ${manifest.pages[0].site_revision}.\n`,
    );
  } catch (error) {
    process.stderr.write(`capture failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}


const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) await main();
