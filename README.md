# Digital Equity page-aware Website Guide

This repository publishes text views of the public Digital Equity sources used by its Website Guide. The August 17 inventory contains 138 public HTML routes drawn from the Wix sitemaps, blog feed, pagination links, and public member links. Each published route is regenerated from the same approved title, description, and content blocks available to retrieval; image markup, visual captions, Wix styling, scripts, forms, tokens, trackers, and authenticated services are not published. The reviewed rendered captures remain private build inputs for source completeness and integrity checks.

The source text remains readable when the model service is unavailable. In that state, the static GitHub Pages build uses the public index for page context and links visitors to source pages. The published Pages configuration calls a separate Railway backend at `https://guide-api-production-a1a1.up.railway.app`. That service holds the provider key, accepts the `https://zmuhls.github.io` browser origin, and applies per-client and shared daily model-call limits. The server preloads GLM-5.2 at startup. The Pages and Wix clients repeat the same empty warm-up request when the guide loads, while a server-side cooldown collapses visitors into one provider call and keeps the model ready for 30 minutes.

## Source limits

The index is a public-site inventory, not a claim that every URL can support an answer. The current crawl contains:

- 90 current operational pages that may support answers.
- 18 excluded pages, including new routes awaiting review, inactive, member, upload, and administrative pages.
- 21 archived pages retained for provenance and historical navigation.
- 9 navigation records that can lead to another page but cannot establish current service facts.

Old posts, category archives, past Tech Fair pages, member surfaces, test pages, duplicate services, and archive-labelled classes do not support participant answers. Dates, locations, registration, availability, eligibility, and inventory can change. The guide refreshes the live downloadable calendar and sends visitors to the current Digital Equity page or staff when the source does not confirm a changing detail.

Every index record carries its canonical URL, authority state, content hash, proposed content owner, and Fortune-review status. The crawler keeps excluded and archived records in the inventory so reviewers can see the full routing scope.

The [August 17 source-refresh report](docs/SOURCE-REFRESH-2026-08-17.md) records the exact FAQ, workshop, route, authority, and capture changes from the prior inventory.

## Page-aware chat

The generated mock site uses the canonical path for each indexed URL. Opening the guide on a class, device, support, calendar, event, program, news, or archive page changes the guide heading, suggested questions, and page context. The interface keeps the initial state small: one question field, an explicit privacy notice, and a few prompts drawn from the current page.

The guide stays compact: two page-specific actions, one question field, a short privacy notice, and collapsed **Info** and source details. Use `?open=1` on a demonstration URL to open it for review.

After a question:

1. The browser starts a credential-free warm-up request while the visitor reads the page. The backend sends Ollama's documented empty preload request and keeps the model loaded for the configured period.
2. The browser sends the question, a short in-memory history, and the canonical current-page URL, path, and title.
3. The privacy gate holds likely personal information before retrieval or model use. A standalone six-digit value is treated as a possible Fortune ID.
4. Vague requests such as **help**, **device**, **class**, and **internet** still invoke the model and receive one short model-authored clarifying question.
5. The server checks the approved record for the current page first. A strong local match narrows the model to that record instead of emitting a fixed sentence.
6. When the current page cannot answer, retrieval ranks up to ten usable answer-authority pages from the wider public index. All 90 answer-authority records are addressable by public title. With no lexical match, the model receives a bounded set of approved current pages and must ask one useful question rather than receiving a server-written fallback.
7. Every valid, non-private new request reaches GLM-5.2 with the resolved question, recent safe conversation context, and bounded approved page excerpts. The model returns one allowed page ID plus a concise answer, or `ASK`. The server checks the response shape and source ID; clarification text additionally rejects raw links or hidden-instruction language. It does not run model prose through a personal-information or grounding classifier. Provider, quota, or twice-invalid outputs are operational errors and never become fabricated Guide turns.
8. Every answer adds another useful page, the staff route, and a way to continue asking questions. The browser never receives `OLLAMA_API_KEY`.

The latest completed user question includes **Edit**. The original question and answer stay visible while the visitor edits. **Update** branches from the preceding bounded context without reusing the old server conversation, and replaces the visible pair only after the revised request succeeds. **Start over** clears the tab's local conversation, continuation token, and saved session state without deleting any transcript already retained by an authorized evaluation deployment. The Wix element follows the same behavior.

Archive, navigation, and excluded routes still receive a tailored guide. Their page text cannot become factual answer authority. The guide moves the visitor to a current operational page.

## Privacy

The browser holds a message containing a likely six-digit Fortune ID before adding it to chat history or making a network request. The backend applies the same hold before retrieval or a model call. Actual disclosed names, contact values, case identifiers, dates of birth, addresses, diagnoses, passwords, and similar private values follow the same pre-model route. General phrases such as “my email is not working,” “I forgot my password,” or “my health” do not trigger the hold. A held value is not added as a fake chat turn and does not erase the preceding safe conversation. The compact participant interface does not show a standing capture or privacy banner; a concise corrective status appears only after a held submission.

Conversation capture is off by default. With `FORTUNE_CONVERSATION_CAPTURE=none`, the server writes no query log and needs no chat database. Browser history is capped at eight recent exchanges (sixteen messages) in tab-scoped session storage so it survives navigation between replica pages without being shared across tabs. The ninth request receives all eight prior exchanges; after it completes, only the oldest exchange is dropped. Open-ended questions sent to the active model must use public or invented information.

An evaluation deployment may select `metadata` or `transcript` capture after Fortune approves the purpose, reviewers, and retention period. Metadata mode stores identifiers and bounded routing/result fields without question or answer text. It also records server-owned interaction labels: opening or follow-up, request type, request and response language, retrieval scope, app version, prompt-policy version, and explicit automation provenance. Transcript mode stores the question and answer only when the automated privacy hold classifies the turn as clear. A clear human request that fails before an answer completes retains its visitor question and failure metadata, but never fabricates or stores an assistant answer. Blocked and sensitive turns keep metadata but no message content. Fortune approved a production human-conversation review pilot on August 21, 2026. Formal `benchmark`, legacy `synthetic`, and direct API traffic stay excluded. Browser automation that deliberately uses the public `replica` or `wix` surface remains reviewable but is visibly labeled **Automated**; provenance is never guessed from transcript wording. Capture mode does not rewrite or add copy to the compact participant interface. The hold is not guaranteed anonymization. Captured conversations expire after 90 days. See [the conversation-capture deployment contract](deployment/CONVERSATION-CAPTURE.md).

Internal Drive notes and meeting transcripts may shape navigation, ambiguity, transparency, and handoff tests. They are not participant-facing factual sources. A statement enters the public answer index only after Fortune assigns a source URL, owner, approval date, and next review date. See [deployment/TRANSCRIPT-INGESTION.md](deployment/TRANSCRIPT-INGESTION.md).

## Evaluation workspace

Railway serves a separate `/evaluation` workspace for approved, privacy-clear human transcripts. The database seeds one admin slot and three editor slots with no email, password, or invitation token. Every authenticated evaluator sees and updates the same shared workspace: **Success**, **Needs work**, the virtual **Not yet reviewed** area, and custom buckets. Moves use optimistic versions, persist in PostgreSQL, and append a transcript-free audit event attributed to the evaluator who made the change.

The workspace lists privacy-clear, unexpired conversations from the public `replica` or `wix` surfaces. It shows complete two-message turns and clear failed attempts; failures from earlier releases may have metadata only, while new failures retain the visitor question without an invented assistant reply. Blocked and sensitive turns remain hidden. Formal benchmark, synthetic, and direct API traffic are excluded. Public-surface automation is retained with an **Automated** badge and source label so evaluators can distinguish it without losing its transcript. Cards state how many turns are grouped into each browser conversation, show failed-attempt counts, and refresh from the shared database while reviewers work. Cards and transcript details display stored timestamps, the prompt-policy version, and the deployed app version. Shared conversation notes and message annotations can mark content as helpful, unclear, incorrect, a safety concern, or other; all evaluators see the same bucket placements and reviewer data. Audit records retain the acting evaluator. Annotation records reference message IDs and never copy transcript text into evaluation or audit tables. Invitation tokens are generated only when an operator deliberately assigns a slot. See [the evaluation deployment contract](deployment/EVALUATION-WORKSPACE.md).

Prompts adds a shared, review-only place to suggest changes to four
presentation modules. It cannot change grounding, privacy, validation, or the
deployed prompt. See the [versioned prompt history](prompts/README.md) and the
[Meeting 4 intervention report](docs/MEETING-4-INTERVENTIONS.md).

Run the content-free aggregate release gate with `DATABASE_URL` supplied through the environment:

```bash
python3 scripts/audit_conversation_quality.py
```

## Local commands

Run the key-free tests and check that the index can produce all route shells:

```bash
./run.sh test
python3 scripts/build_pages.py --check-index
```

The test launcher runs the Python unit suite across retrieval, API contracts, privacy, source authority, grounding, conversation persistence, the crawler, the Pages builder, production limits, warm-up behavior, responsive answer expansion, member access, styling safeguards, and Wix secret handling. It then runs browser-core, bridge, and snapshot-capture safety tests.

The [Website Guide evaluation suite](evals/website-guide/README.md) adds a fixed 41-case synthetic benchmark across broad and specific intent, typos, multilingual requests, privacy, adversarial input, page awareness, follow-up context, and input boundaries. Its executable gates are stricter than the unit tests and produce a versioned run record for staff review.

Build the static GitHub Pages output:

```bash
python3 scripts/build_pages.py
python3 -m http.server 8791 --directory _site
```

The build writes 138 human-readable, text-only `index.html` source routes under `_site/`, including the root route, and copies only the shared files that those views and the sidecar require.

Refresh the reviewed current calendar before a calendar release:

```bash
node scripts/capture_calendar_agenda.mjs --output /tmp/fortune-calendar-agenda.json
python3 scripts/refresh_calendar_source.py --agenda /tmp/fortune-calendar-agenda.json
python3 scripts/build_pages.py
```

The capture records the public Daily Agenda's visible week and class rows, plus the visible downloadable-PDF action. The refresh validates that action against the newly downloaded official PDF, extracts the published monthly schedule, and writes one committed `calendar-source.json` record. Both static deployments consume that same record. The mirror never stores capacity, creates booking URLs, or proxies registration; each registration action opens Fortune's live calendar.

Run the live local model demo:

```bash
export OLLAMA_API_KEY="your Ollama Cloud key"
./run.sh
```

The launcher uses `http://127.0.0.1:8790`, leaves an occupied port untouched, and keeps the credential in the server process.

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
- [Copilot Studio bridge](deployment/wix/copilot-studio-bridge/README.md) is an optional, separately hosted Direct Line embed for evaluating Fortune's Microsoft agent on Wix without exposing its channel secret. It is limited to approved public information and does not replace the guide's pre-provider privacy and source-authority checks.
- [GitHub Pages roadmap](deployment/github-pages/ROADMAP.md) describes the public replica, the source-backed static state, the active-model backend, and the review gates before sharing the URL with Jacob and the Fortune team.

The Pages publication workflow is [`.github/workflows/pages.yml`](.github/workflows/pages.yml). It builds the allowlisted `_site/` directory and deploys that artifact after changes reach `main` or an authorized manual run begins.

The provider remains behind the server contract. Fortune can later move from the Ollama meeting provider to its approved Microsoft route without rebuilding the participant interface.

## GitHub publication

The demonstration has a dedicated public repository at [zmuhls/fortune-digital-equity-guide-demo](https://github.com/zmuhls/fortune-digital-equity-guide-demo). Its repository root contains only the demonstration source, tests, workflows, and deployment notes. GitHub Actions builds the allowlisted static artifact and publishes it at [zmuhls.github.io/fortune-digital-equity-guide-demo](https://zmuhls.github.io/fortune-digital-equity-guide-demo/). The public Pages version uses the HTTPS model backend configured in `config.js`. If that service is unavailable, the text source pages and navigation remain readable, while chat reports that it is unavailable instead of substituting an unlogged browser answer.

## Suggested meeting path

1. Open a route with `?open=1` and press one page-specific starter.
2. Open a second mock route and show that its page context changes while the conversation remains available in the same tab.
3. Ask a page-specific question and follow the related route to another mock page.
4. Enter `device` to show one clarifying question.
5. Ask about an Excel topic to show retrieval of a specific class page.
6. Enter `123456` to show the pre-model Fortune ID privacy hold.
7. Stop the backend and show that the static page context and source links remain available.
