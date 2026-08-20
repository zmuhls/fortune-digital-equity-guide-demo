# Public-site snapshot capture

The snapshot command reads every route from `site-index.json` and opens each
public URL in Firefox at 1440 by 1200 pixels. It scrolls through the rendered
page so lazy images and widgets can appear, expands public collection controls
such as **Load more**, and captures each Wix accordion response before scripts
are removed. It upgrades each image to its resolved `currentSrc`, then
serializes the main frame. Firefox rejects cookies for the capture, and every
route uses the same user agent and language.

Before serialization, the command removes scripts, templates, preload hints,
meta refreshes, token-bearing Wix data, inline handlers, executable URLs,
objects, embeds, and `srcdoc`. Public accordion answers become native open
`<details>` blocks and public header submenus become native static disclosures;
they must never be published as disabled controls with hidden text. A fully
expanded collection has its exhausted Load more control removed rather than
published as a dead button. Visible submit, upload, register, book, and similar
action controls become direct links to the original public page; remaining form
fields become inert. A visible iframe becomes an inert screenshot linked to its original
public content; when a screenshot cannot be captured, its outbound link remains
as a labeled placeholder. This preserves public text and links without
publishing live submission, booking, or Wix runtime behavior. Each result also
receives `noindex` and `no-referrer` directives.

Install the pinned browser package and Firefox once:

```sh
npm ci
npx playwright install firefox
```

Run the complete capture:

```sh
npm run capture:replica -- --concurrency 2
```

The command stages all files in a temporary directory. It publishes
`replica-snapshots/<page-id>.html.gz` and `replica-manifest.json` only after
every indexed route succeeds with its expected status and every page reports
the same numeric Wix revision. Existing output remains in place when a route
fails.

The manifest records `static_content` for every route: materialized Wix
accordions, static navigation menus, and progressive-collection expansion
counts (including visible image counts for image-only galleries). Treat a revision or sitemap match as an inventory check only, not proof
that dynamic content is current. A release needs a rendered-capture review of
the FAQ/support disclosures and dynamic catalog/calendar content.

For a bounded network smoke check, choose indexed routes and a separate output
directory:

```sh
npm run capture:replica -- \
  --route / \
  --route /about \
  --output-dir /tmp/fortune-replica-smoke
```

`source_bytes` and `source_sha256` describe the sanitized UTF-8 HTML before
compression. `snapshot_bytes` and `snapshot_sha256` describe the deterministic
gzip file produced by the pinned pure-JavaScript compressor. Setting
`SOURCE_DATE_EPOCH` also fixes the manifest's `captured_at` value for
reproducible builds.

An expected non-200 response requires an exact route exception and a separate
output directory:

```sh
npm run capture:replica -- \
  --allow-status https://www.fortunedigitalequity.org/example=404 \
  --output-dir /tmp/fortune-status-check
```

Production artifacts contain status 200 for all indexed routes. A capture that
reads an alternate `--index` also requires a separate output directory.

## Updating the Guide's source index

Raw Wix HTML is not an acceptable factual source for the Guide: it can omit
accordion, lazy-loaded, and progressive collection text. After a complete,
reviewed capture, refresh the index from the manifest-bound rendered snapshots:

```sh
python3 scripts/rebuild_site_index.py --from-rendered-snapshots
```

This command makes no network request. It verifies every compressed and
expanded snapshot hash against `replica-manifest.json`, requires exact route
parity, and preserves each page's authority/provenance metadata while replacing
only the rendered title, description, headings, blocks, and internal links.
Do not accept a raw-crawl disappearance from an existing public page as a site
change without a rendered comparison.

`./run.sh index` uses this safe rendered-snapshot path. `./run.sh crawl-index`
is reserved for an inventory candidate and must not be treated as a factual
corpus refresh until its routes and rendered text have been reviewed.

## Railway bundle and release checks

Railway receives the compact raw snapshot bundle, not the ignored compressed
snapshot directory. Rebuild it from the reviewed manifest before a Railway
release:

```sh
python3 scripts/pack_deploy_snapshots.py
```

The packer verifies that every bundle member expands to the exact reviewed
source hash and atomically replaces `replica-snapshots.raw.tar.xz`. The
Railway predeploy restore script verifies the same contract before building the
static pages.

Before publishing, run the full capture, rendered-index refresh, bundle pack,
static build, and test suite. Review at least the home/contact FAQs, support
hours, Workshops/Catalog full collection, Calendar expanded events, and header
Services/Resources links in the generated static output. The Calendar records a
nine-click snapshot horizon and a link to Fortune's live agenda; booking and
form submission remain live-site actions, and the mirror must not simulate them.
