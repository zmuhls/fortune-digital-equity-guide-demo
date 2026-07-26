# Fortune Digital Equity page-aware guide

This demonstration builds a mock route for every public URL in the Fortune Digital Equity Wix sitemap. The July 20 index contains 184 routes. Each route uses the page title, public source text, authority state, and related links from `site-index.json`. The sidecar opens with a question about the current page and can direct the visitor to another relevant section.

The page remains readable when the model service is unavailable. In that state, the static GitHub Pages build uses the public index for page context and links visitors to source pages. The published Pages configuration calls a separate Railway backend at `https://guide-api-production-a1a1.up.railway.app`. That service holds the provider key, accepts the `https://zmuhls.github.io` browser origin, and applies per-client and shared daily model-call limits. The server preloads GLM-5.2 at startup. The Pages and Wix clients repeat the same empty warm-up request when the guide loads, while a server-side cooldown collapses visitors into one provider call and keeps the model ready for 30 minutes.

## Source limits

The index is a public-site inventory, not a claim that every URL can support an answer. The current crawl contains:

- 147 current operational candidates. Three service pages returned partial responses and cannot support answers, leaving 144 content-complete answer sources in the running demo.
- 17 excluded pages, including test, member, upload, duplicate, sample, and outdated pages.
- 13 archived pages retained for provenance and historical navigation.
- 7 navigation records that can lead to another page but cannot establish current service facts.

Old posts, category archives, past Tech Fair pages, member surfaces, test pages, duplicate services, and archive-labelled classes do not support participant answers. Dates, locations, registration, availability, eligibility, and inventory can change. The guide sends visitors to the current Fortune page or staff for confirmation.

Every index record carries its canonical URL, authority state, content hash, proposed content owner, and Fortune-review status. The crawler keeps excluded and archived records in the inventory so reviewers can see the full routing scope.

## Page-aware chat

The generated mock site uses the canonical path for each indexed URL. Opening the guide on a class, device, support, calendar, event, program, news, or archive page changes the guide heading, suggested questions, and page context. The interface keeps the initial state small: one question field, an explicit privacy notice, and a few prompts drawn from the current page.

After a question:

1. The browser starts a credential-free warm-up request while the visitor reads the page. The backend sends Ollama's documented empty preload request and keeps the model loaded for the configured period.
2. The browser sends the question, a short in-memory history, and the canonical current-page URL, path, and title.
3. The privacy gate holds likely personal information before retrieval or model use. A standalone six-digit value is treated as a possible Fortune ID.
4. Known vague requests such as **help**, **device**, **class**, and **internet** receive one short clarifying question.
5. The server checks the approved record for the current page. When that record contains matching evidence, it is the only factual record sent to the model.
6. When the current page cannot answer, retrieval searches the wider approved public index. When that search finds no matching evidence, the model is not called and the guide sends the visitor to staff.
7. GLM-5.2 on Ollama Cloud selects from the supplied source IDs. The server validates that choice and builds the visible factual answer from sentences in the selected website record. Model-written factual prose is never shown.
8. Every answer adds another useful page, the staff route, and a way to continue asking questions. The browser never receives `OLLAMA_API_KEY`.

Archive, navigation, and excluded routes still receive a tailored guide. Their page text cannot become factual answer authority. The guide moves the visitor to a current operational page.

## Privacy

The guide tells visitors: **Do not enter your six-digit Fortune ID, name, phone number, email, address, case details, or other personal information.** The browser replaces a message containing a likely six-digit Fortune ID with a privacy notice before adding it to chat history or making a network request. The backend applies the same hold before retrieval or a model call. Names, contact details, case information, health information, passwords, and similar details follow the same pre-model route.

The local server writes no query log and has no chat database. Browser history exists only in memory for the current tab and is capped at six turns. Open-ended questions sent to the active model must use public or invented information.

Internal Drive notes and meeting transcripts may shape navigation, ambiguity, transparency, and handoff tests. They are not participant-facing factual sources. A statement enters the public answer index only after Fortune assigns a source URL, owner, approval date, and next review date. See [deployment/TRANSCRIPT-INGESTION.md](deployment/TRANSCRIPT-INGESTION.md).

## Local commands

Run the key-free tests and check that the index can produce all route shells:

```bash
./run.sh test
python3 scripts/build_pages.py --check-index
```

The test launcher runs 79 Python unit tests across retrieval, API contracts, privacy, source authority, grounding, the crawler, the Pages builder, production limits, warm-up behavior, responsive answer expansion, member access, styling safeguards, view modes, answer formatting, and Wix secret handling. It then runs 13 browser-core unit tests for page families, prompts, staged evidence, route generation, view modes, answer formatting, and client-side redaction.

Build the static GitHub Pages output:

```bash
python3 scripts/build_pages.py
python3 -m http.server 8791 --directory _site
```

The build writes 184 `index.html` route shells under `_site/`, including the root route. Six shared browser and index files remain at the build root. The complete artifact contains 190 files.

Run the live local model demo:

```bash
export OLLAMA_API_KEY="your Ollama Cloud key"
./run.sh
```

The launcher uses `http://127.0.0.1:8790`, leaves an occupied port untouched, and keeps the credential in the server process.

Local development defaults to **Admin view**. Its **Preview as** filter opens a true Public view of the page. Deployed hosts default to **Public view**, which keeps model names and readiness diagnostics out of the participant interface. Add `?view=admin` or `?view=public` to override the default; explicit view modes persist across mock-site navigation.

Refresh the public Wix index manually when a source review is planned:

```bash
./run.sh index
python3 scripts/build_pages.py --check-index
./run.sh test
```

The refresh obeys `robots.txt`, rate-limits requests, retries `429` responses, and rewrites `site-index.json`. Review content-hash changes, authority changes, removed URLs, partial responses, and volatile service information before accepting the refreshed file.

## Weekly source review

The repository scaffold includes a Monday 13:17 UTC index-refresh check and a manual dispatch in [`.github/workflows/refresh-index.yml`](.github/workflows/refresh-index.yml). The check preserves the checked-in index as `baseline-site-index.json`, creates a refreshed `site-index.json`, validates that it can build all route shells, and uploads both files for 14 days in an artifact named `fortune-site-index-review-<run number>`.

The refresh check has read-only repository permission. It does not commit, push, deploy, or treat changed public text as approved. A reviewer compares the two index files, confirms source authority and volatile claims with Fortune staff, then deliberately accepts any approved update and rebuilds `_site/`.

## Wix and GitHub Pages

The [deployment overview](deployment/README.md) carries the shared API contract.

- [Wix app subset](wix-app/README.md) contains the administrator key form, Admin-only Wix Secrets Manager methods, backend-only secret reader, embedded-script fragment, and site guide element. [The earlier roadmap](deployment/wix/ROADMAP.md) retains the extension-selection history.
- [GitHub Pages roadmap](deployment/github-pages/ROADMAP.md) describes the 184-route public mock, the source-backed static state, the active-model backend, and the review gates before sharing the URL with Jacob and the Fortune team.

The Pages publication workflow is [`.github/workflows/pages.yml`](.github/workflows/pages.yml). It builds the allowlisted `_site/` directory and deploys that artifact after changes reach `main` or an authorized manual run begins.

The provider remains behind the server contract. Fortune can later move from the Ollama meeting provider to its approved Microsoft route without rebuilding the participant interface.

## GitHub publication

The demonstration has a dedicated public repository at [zmuhls/fortune-digital-equity-guide-demo](https://github.com/zmuhls/fortune-digital-equity-guide-demo). Its repository root contains only the demonstration source, tests, workflows, and deployment notes. GitHub Actions builds the allowlisted static artifact and publishes it at [zmuhls.github.io/fortune-digital-equity-guide-demo](https://zmuhls.github.io/fortune-digital-equity-guide-demo/). The public Pages version uses the HTTPS model backend configured in `config.js` and falls back to the source-backed browser guide whenever that service is unavailable or its public usage limit has been reached.

## Suggested meeting path

1. Open two different mock routes and show that the sidecar title and prompts follow the current page.
2. Ask a page-specific question and follow the related route to another mock page.
3. Enter `device` to show one clarifying question.
4. Ask about an Excel topic to show retrieval of a specific class page.
5. Enter `123456` to show the pre-model Fortune ID privacy hold.
6. Stop the backend and show that the static page context and source links remain available.
