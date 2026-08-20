#!/usr/bin/env node

import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { gunzipSync } from "node:zlib";
import test from "node:test";

import {
  CaptureError,
  CALENDAR_STATIC_HORIZON_EXPANSIONS,
  MAX_PROGRESSIVE_COLLECTION_EXPANSIONS,
  MAX_TRANSIENT_NAVIGATION_ATTEMPTS,
  SOURCE_ORIGIN,
  assertSanitized,
  assertStaticContentMaterialized,
  collectionMetricAdvanced,
  isTransientNavigationFailure,
  atomicPublish,
  buildManifest,
  capturedAt,
  deterministicGzip,
  parseArgs,
  recoverInterruptedPublications,
  sameFilesystemPath,
  selectRoutes,
  sha256,
  validateCapturedPages,
  validateIndex,
} from "../scripts/capture_replica.mjs";


function indexDocument(pages) {
  return { unique_urls: pages.length, pages };
}


const HOME = {
  url: `${SOURCE_ORIGIN}/`,
  id: "page-home-e6c04f0f",
};
const ABOUT = {
  url: `${SOURCE_ORIGIN}/about`,
  id: "page-about-59ff9683",
};


test("the current index contains the declared number of unique safe routes", async () => {
  const realIndex = JSON.parse(
    await readFile(new URL("../site-index.json", import.meta.url), "utf8"),
  );
  const routes = validateIndex(realIndex);
  assert.equal(routes.length, realIndex.unique_urls);
  assert.equal(new Set(routes.map((route) => route.path)).size, routes.length);
  assert.equal(new Set(routes.map((route) => route.id)).size, routes.length);
});


test("index validation rejects incomplete and duplicate route inventories", () => {
  assert.throws(
    () => validateIndex({ unique_urls: 2, pages: [HOME] }),
    /site index is incomplete/,
  );
  assert.throws(
    () => validateIndex(indexDocument([HOME, { ...HOME, id: "copy" }])),
    /duplicate page URL/,
  );
  assert.throws(
    () => validateIndex(indexDocument([HOME, { ...ABOUT, id: HOME.id }])),
    /duplicate page id/,
  );
  assert.throws(
    () => validateIndex(indexDocument([{ ...ABOUT, url: "https://example.org/about" }])),
    /must be a public/,
  );
});


test("partial selection requires a separate output directory", () => {
  const routes = validateIndex(indexDocument([HOME, ABOUT]));
  const options = parseArgs(["--route", "/"]);
  assert.throws(() => selectRoutes(routes, options), /partial capture requires --output-dir/);

  const smoke = parseArgs(["--route", "/", "--output-dir", "/tmp/fortune-snapshot-test"]);
  assert.deepEqual(selectRoutes(routes, smoke).selected, [routes[0]]);
});


test("alternate indexes and status exceptions cannot replace canonical output", () => {
  const routes = validateIndex(indexDocument([HOME, ABOUT]));
  const alternate = parseArgs(["--index", "/tmp/alternate-site-index.json"]);
  assert.throws(
    () => selectRoutes(routes, alternate),
    /alternate --index requires --output-dir/,
  );
  const statusException = parseArgs([
    "--allow-status",
    `${SOURCE_ORIGIN}/about=404`,
  ]);
  assert.throws(
    () => selectRoutes(routes, statusException),
    /status exception requires --output-dir/,
  );
});


test("filesystem identity resolves an output symlink", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "fortune-replica-path-test-"));
  const target = path.join(root, "target");
  const alias = path.join(root, "alias");
  await mkdir(target);
  await symlink(target, alias, "dir");
  try {
    assert.equal(sameFilesystemPath(alias, target), true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("status exceptions are explicit URL and status pairs", () => {
  const options = parseArgs([
    "--allow-status",
    `${SOURCE_ORIGIN}/about=404`,
    "--output-dir",
    "/tmp/fortune-snapshot-test",
  ]);
  assert.equal(options.allowedStatuses.get(`${SOURCE_ORIGIN}/about`), 404);
  assert.throws(() => parseArgs(["--allow-status", "/about"]), /URL=STATUS/);
  assert.throws(
    () => parseArgs(["--allow-status", `${SOURCE_ORIGIN}/about=200`]),
    /only for an expected non-200/,
  );
  assert.throws(
    () => parseArgs(["--allow-status", `${SOURCE_ORIGIN}/about=999`]),
    /HTTP status from 100 through 599/,
  );
});


test("gzip output and hashes are deterministic", async () => {
  const source = Buffer.from("<!doctype html><title>Replica</title>\n", "utf8");
  const first = await deterministicGzip(source);
  const second = await deterministicGzip(source);
  assert.deepEqual(first, second);
  assert.deepEqual(gunzipSync(first), source);
  assert.equal(sha256(first), sha256(second));
  assert.equal(first[0], 0x1f);
  assert.equal(first[1], 0x8b);
  assert.deepEqual([...first.subarray(4, 8)], [0, 0, 0, 0]);
  assert.equal(
    sha256(first),
    "b309faa27c633081f0ab43382bccc2227e12455b45dbfd5ca13041170c81e0a2",
  );
});


test("sanitized markup guard accepts inert HTML and rejects executable or secret markup", () => {
  const safe = '<!doctype html><html><head><meta name="robots" content="noindex,nofollow,noarchive"><meta name="referrer" content="no-referrer"></head><body><p>Public page</p></body></html>';
  assert.doesNotThrow(() => assertSanitized(safe));
  assert.doesNotThrow(() => assertSanitized(safe.replace("<p>", '<img src="data:image/png;base64,iVBORw0KGgo="><p>')));
  const unsafe = [
    "<script src=x></script>",
    "<template>hidden</template>",
    '<meta http-equiv="refresh" content="0;url=/">',
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">go</a>',
    '<a href="data:text/html,unsafe">go</a>',
    '<a href="vbscript:msgbox(1)">go</a>',
    '<iframe srcdoc="<p>x</p>"></iframe>',
    '<form action="/collect"><input name="email"></form>',
    "<button>Submit</button>",
    '<iframe src="/about" sandbox="allow-scripts"></iframe>',
    '<div id="wix-viewer-model"></div>',
    '<div data-value="X-XSRF-TOKEN"></div>',
    '<style>:root{--cookie-banner-primary-color:#fff}</style>',
    '<p>An error occurred. Try again later</p>',
    '<p>Your content has been submitted</p>',
    '<p>Widget Didn\'t Load</p>',
  ];
  for (const fragment of unsafe) {
    assert.throws(
      () => assertSanitized(`<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">${fragment}`),
      CaptureError,
    );
  }
});


test("static disclosure and navigation gates reject hidden Wix-only content", () => {
  const materialized = `
    <details data-replica-static-disclosure="true" open><summary>FAQ</summary><div data-replica-static-content="true">Answer</div></details>
    <details data-replica-static-menu="true"><summary>Services</summary><ul data-replica-static-menu-content="true"><li><a href="/workshops">Workshops</a></li></ul></details>
  `;
  assert.doesNotThrow(() => assertStaticContentMaterialized(materialized, {
    disclosures: 1,
    navigationMenus: 1,
  }));
  assert.throws(
    () => assertStaticContentMaterialized(
      materialized.replace(" open><summary>FAQ", "><summary>FAQ"),
      { disclosures: 1, navigationMenus: 1 },
    ),
    /not open/,
  );
  assert.throws(
    () => assertStaticContentMaterialized(
      `${materialized}<button data-hook="accordion-item-header">FAQ</button>`,
      { disclosures: 1, navigationMenus: 1 },
    ),
    /Wix accordion header remains/,
  );
  assert.throws(
    () => assertStaticContentMaterialized(materialized, {
      disclosures: 1,
      navigationMenus: 2,
    }),
    /static navigation menus; expected 2/,
  );
});


test("collection capture uses an explicit finite static calendar horizon", () => {
  assert.equal(CALENDAR_STATIC_HORIZON_EXPANSIONS, 9);
  assert.ok(MAX_PROGRESSIVE_COLLECTION_EXPANSIONS > CALENDAR_STATIC_HORIZON_EXPANSIONS);
  const baseline = { visible_text_characters: 748, links: 9, service_page_links: 0, images: 7 };
  assert.equal(collectionMetricAdvanced(baseline, { ...baseline, images: 55 }), true);
  assert.equal(collectionMetricAdvanced(baseline, baseline), false);
});


test("only transient transport failures qualify for a bounded navigation retry", () => {
  assert.equal(MAX_TRANSIENT_NAVIGATION_ATTEMPTS, 3);
  assert.equal(isTransientNavigationFailure(new Error("page.goto: NS_ERROR_UNKNOWN_HOST")), true);
  assert.equal(isTransientNavigationFailure(new Error("net::ERR_CONNECTION_RESET")), true);
  assert.equal(isTransientNavigationFailure(new Error("Navigation timeout of 90000ms exceeded")), true);
  assert.equal(isTransientNavigationFailure(new Error("https://example.test returned 404; expected 200")), false);
  assert.equal(isTransientNavigationFailure(new Error("snapshot has unsafe markup")), false);
});


test("captured route validation requires exact order and one numeric Wix revision", () => {
  const routes = validateIndex(indexDocument([HOME, ABOUT]));
  const page = (route, revision = 1837) => ({
    ...route,
    file: `replica-snapshots/${route.id}.html.gz`,
    site_revision: revision,
  });
  assert.equal(validateCapturedPages(routes, routes.map((route) => page(route))), 1837);
  assert.throws(
    () => validateCapturedPages(routes, [page(routes[0]), page(routes[1], 1838)]),
    /mixed or missing Wix revisions/,
  );
});


test("manifest keeps the capture and page contract", () => {
  const pages = [{ id: HOME.id }];
  const manifest = buildManifest({
    timestamp: "2026-08-03T12:00:00.000Z",
    browserVersion: "128.0",
    pages,
  });
  assert.deepEqual(Object.keys(manifest), [
    "captured_at",
    "source_origin",
    "route_count",
    "capture",
    "pages",
  ]);
  assert.deepEqual(manifest.capture.browser, { name: "firefox", version: "128.0" });
  assert.deepEqual(manifest.pages, pages);
});


test("SOURCE_DATE_EPOCH makes captured_at reproducible", () => {
  assert.equal(capturedAt({ SOURCE_DATE_EPOCH: "0" }), "1970-01-01T00:00:00.000Z");
  assert.equal(
    capturedAt({}, new Date("2026-08-03T12:34:56.000Z")),
    "2026-08-03T12:34:56.000Z",
  );
  assert.throws(() => capturedAt({ SOURCE_DATE_EPOCH: "yesterday" }), /non-negative integer/);
});


test("atomic publication replaces both artifacts and removes prior files", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "fortune-replica-publish-test-"));
  const staging = path.join(root, "stage");
  const output = path.join(root, "output");
  await mkdir(path.join(staging, "replica-snapshots"), { recursive: true });
  await mkdir(path.join(output, "replica-snapshots"), { recursive: true });
  await writeFile(path.join(staging, "replica-snapshots", "new.html.gz"), "new");
  await writeFile(path.join(staging, "replica-manifest.json"), "new manifest");
  await writeFile(path.join(output, "replica-snapshots", "old.html.gz"), "old");
  await writeFile(path.join(output, "replica-manifest.json"), "old manifest");
  try {
    await atomicPublish(staging, output);
    assert.equal(await readFile(path.join(output, "replica-manifest.json"), "utf8"), "new manifest");
    assert.equal(await readFile(path.join(output, "replica-snapshots", "new.html.gz"), "utf8"), "new");
    await assert.rejects(readFile(path.join(output, "replica-snapshots", "old.html.gz")), /ENOENT/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("startup recovery restores the prior pair after an interrupted publication", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "fortune-replica-recovery-test-"));
  const suffix = `${process.pid}-aaaaaaaaaa`;
  const snapshots = path.join(root, "replica-snapshots");
  const manifest = path.join(root, "replica-manifest.json");
  const backupSnapshots = path.join(root, `.replica-snapshots.previous-${suffix}`);
  const backupManifest = path.join(root, `.replica-manifest.previous-${suffix}.json`);
  const transaction = path.join(root, `.replica-publish-${suffix}.json`);
  await mkdir(snapshots);
  await mkdir(backupSnapshots);
  await writeFile(path.join(snapshots, "new.html.gz"), "new snapshot");
  await writeFile(path.join(backupSnapshots, "old.html.gz"), "old snapshot");
  await writeFile(backupManifest, "old manifest");
  await writeFile(transaction, JSON.stringify({
    suffix,
    old_snapshots: true,
    old_manifest: true,
  }));
  try {
    await recoverInterruptedPublications(root);
    assert.equal(await readFile(path.join(snapshots, "old.html.gz"), "utf8"), "old snapshot");
    assert.equal(await readFile(manifest, "utf8"), "old manifest");
    await assert.rejects(readFile(transaction), /ENOENT/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
