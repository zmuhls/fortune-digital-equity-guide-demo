# GitHub Pages demonstration roadmap

GitHub Pages hosts a human-readable text view for each of the 138 public HTML routes in `site-index.json`, so a reviewer can open a class, device, support, calendar, event, news, or archive path directly. Current answer routes expose the same approved title, description, and content blocks available to retrieval; navigation, archive, and excluded routes do not publish stale answer text. The reviewed Wix captures remain build inputs and are not served as page markup.

GitHub Pages serves static files and cannot protect a runtime model credential. The public site therefore has two operating states.

## Static source-backed state

The Pages build contains small semantic HTML documents, one minimal text-view stylesheet, the Website Guide JavaScript, `site-index.json`, and an empty or public API-base configuration. It publishes no page images, inline visual styling, or Wix asset dependencies. When the backend is absent, the current route still supplies its source text, authority state, tailored prompts, and verified Fortune link.

The static state cannot confirm schedules, registration, availability, eligibility, inventory, or other changing details. It links to the live Fortune page for those facts.

## Active-model state

```text
GitHub Pages route
  -> public HTTPS API base URL from config.js
  -> external chat and retrieval backend
  -> Ollama Cloud with a server-side key
```

The external service holds `OLLAMA_API_KEY` in its environment, enforces the page-first source and privacy rules, and limits browser origins to the Pages URL and approved local development origins. It checks the current page first and searches up to ten other approved pages after a local miss. Every successful non-private new request calls the model. The model receives only bounded approved records and writes a concise grounded answer or one clarifying question; the server validates its selected source, claims, privacy, and response shape. With no lexical match, it receives a bounded navigation set rather than a fixed server-authored fallback. The repository, Pages files, browser requests, and source maps contain no model credential.

The current public API base is `https://guide-api-production-a1a1.up.railway.app`. The Railway `guide-api` service uses the exact GitHub Pages origin, a 30-call hourly model limit per client, a 300-call shared daily model limit, a separate bounded chat-request budget, and `/health` as its deployment healthcheck. Public production capture remains off unless Fortune completes the approval gate in [`../CONVERSATION-CAPTURE.md`](../CONVERSATION-CAPTURE.md).

The provider boundary also allows Fortune to replace the meeting model with an approved Microsoft service without changing the public sidecar.

## Build and inspect locally

From `visualizations/fortune-infobot-wireframes/model-demo`:

```bash
./run.sh test
python3 scripts/build_pages.py --check-index
python3 scripts/build_pages.py
python3 -m http.server 8791 --directory _site
```

The builder creates `_site/index.html` and one nested `index.html` for every non-root canonical path. The complete output contains 138 text-source routes plus the shared text view, sidecar, configuration, and source-index files.

Each route sets public context with `path`, `sourceUrl`, and `pageId`. The small text navigation remains inside the Pages site; the source footer opens the canonical Fortune URL.

## Local live-model check

```bash
export OLLAMA_API_KEY="your Ollama Cloud key"
./run.sh
```

The local service runs at `http://127.0.0.1:8790`. Before publishing, deploy the same Python service or an equivalent API to HTTPS and set only its public base URL in the Pages `config.js`.

Required backend routes are:

- `GET /health` reports service availability, model state, index date, and source count without exposing secrets.
- `POST /api/chat` accepts the shared message/history/page context plus client event and server-issued conversation continuation fields. It returns a model-authored, source-validated answer or clarification, retrieval scope, validated sources, related pages, model-call status, stable UUIDs, and capture status.
- `GET /api/search?q=` provides key-free retrieval over approved answer sources.
- `GET /api/sources` provides the public source inventory used by the demonstration.

## Page and chat requirements

- Every indexed route loads and identifies the correct canonical Fortune page.
- The guide heading and suggested questions follow the route title and page type.
- A clear answer offers at least one related page distinct from the current page.
- Archive, navigation, and excluded pages route visitors to a current operational page without using their text as current service authority.
- Known ambiguous questions still call the model and receive one short, validated clarification, with choices only when they help.
- The question field always warns visitors not to enter a six-digit Fortune ID or other personal information.
- A likely six-digit Fortune ID is removed in the browser before chat history or a request. The backend applies the same pre-model hold.
- Failed or absent model service leaves the static page context, current Fortune source, related routes, and staff contact available.
- The public production build excludes raw transcripts, private Drive notes, API keys, and query logs. Synthetic staging capture uses a separate Railway environment and database.

## Weekly and manual source review

The source-refresh workflow at [`../../.github/workflows/refresh-index.yml`](../../.github/workflows/refresh-index.yml) runs Mondays at 13:17 UTC and supports manual dispatch. It performs these read-only steps:

1. Preserve the checked-in `site-index.json` as `baseline-site-index.json`.
2. Run `python3 scripts/rebuild_site_index.py` against the public Wix sitemap.
3. Run `python3 scripts/build_pages.py --check-index` against the refreshed index.
4. Upload both index files for 14 days as `fortune-site-index-review-<run number>`.

The workflow has `contents: read` permission and does not commit, push, or deploy. A reviewer checks added and removed URLs, content hashes, authority changes, partial responses, and volatile claims. Fortune staff must approve source-authority changes and changing service facts before the refreshed index becomes the Pages source.

A manual local review uses:

```bash
./run.sh index
python3 scripts/build_pages.py --check-index
./run.sh test
```

## Publication sequence

1. Review the 138-route static build locally, including excluded and archived routes.
2. Run the key-free tests, index check, keyboard checks, desktop and mobile browser checks, and a broken-link pass.
3. Publish the source-backed static build through [`../../.github/workflows/pages.yml`](../../.github/workflows/pages.yml).
4. Verify every generated route at the repository Pages base path.
5. Deploy the optional HTTPS model backend and add the exact Pages origin to its allowlist.
6. Set the public `apiBaseUrl` in `config.js`, keep the provider key in the backend environment, and verify both active-model and unavailable-backend states.
7. Share the labeled demonstration URL with Jacob and the Fortune team for review before any Wix installation.

## GitHub repository and Pages URL

The dedicated public repository is [zmuhls/fortune-digital-equity-guide-demo](https://github.com/zmuhls/fortune-digital-equity-guide-demo), and its Pages URL is [zmuhls.github.io/fortune-digital-equity-guide-demo](https://zmuhls.github.io/fortune-digital-equity-guide-demo/). The repository remains separate from the broader `ai4wut` workspace and contains only this demonstration.

## Review with Fortune

Use the Pages URL to review page-specific prompts, rerouting, source authority, ambiguity, privacy language, staff handoff, model status, and the Wix adoption path. Keep the site labeled as a demonstration. Current schedules, eligibility, inventory, and availability remain controlled by the linked Fortune pages and staff.

## Official GitHub references

- [What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [Configure a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
