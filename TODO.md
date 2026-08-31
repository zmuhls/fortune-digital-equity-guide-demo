# Website Guide — next steps

## Shared prompt and nightly review — 2026-08-31

- [x] Make the complete system prompt an editable shared draft in **Prompts**
  for every authenticated evaluator, with required change notes and immutable
  author/time/full-text revision history.
- [x] Keep draft editing separate from runtime activation and display the
  release/edit convention as `v1.33` while retaining the immutable internal
  `2026-08-31-v33` provenance on logged turns.
- [x] Attach a known evaluator name to transcript cards and details, attribute
  future same-origin signed-in sessions automatically, and allow explicit,
  audited assignment of older unattributed conversations without guessing.
- [x] Preserve every note and annotation save/removal in append-only storage
  with evaluator name and timestamp while keeping the current shared value.
- [x] Add a privacy-safe 24-hour review that summarizes prompt activity,
  reviewer feedback, attribution changes, and human/automated aggregates without
  selecting participant message text or exposing account data.
- [x] Push directly to `main`, deploy schema 012 only to the canonical zmuhls
  production service, verify existing transcript/review row counts are intact,
  and activate the 8:00 PM America/New_York review automation.

## Prompt responsiveness and automation provenance — 2026-08-31

- [x] Show the saving evaluator's name and timestamp on every shared note and
  message annotation; preserve attribution through a same-person login reset.
- [x] Publish prompt v32 with evidence-exhausted clarification, current-source
  specificity, concise answers, sitewide page access, and follow-up advancement.
- [x] Compare v32 with v31 on the same isolated 41-case suite with no database
  access: 36/41 versus 22/41 overall, 30/34 versus 20/34 required, fourteen
  paired wins, zero paired losses, and zero excessive-length failures.
- [x] Narrow the personal-information hold to disclosed values; preserve the
  prior safe conversation and add no synthetic privacy transcript turn.
- [x] Remove the repetitive post-model personal-information phrase evaluator
  and its third retry; keep the value-based pre-send hold.
- [x] Keep eight complete exchanges in Pages, Wix, tab persistence, and the
  server prompt, dropping only the oldest exchange after the ninth response.
- [x] Give the collapsed launcher three staggered, hand-drawn monochrome ray
  bursts at fifteen-second intervals, with a reduced-motion opt-out.
- [x] Store explicit automation provenance, show it on shared evaluator cards
  and transcript details, and reconcile historical artifact-backed runs without
  reading or deleting transcript content.
- [x] Preserve v31 as an immutable compiled prompt artifact and update the
  shared Prompts view to the exact v32 runtime policy.
- [x] Pass the complete v33 release suite: 365 Python, 30 frontend, and 18
  source-snapshot checks, plus desktop, mobile, and reduced-motion launcher QA.
- [x] Exclude two stale benchmark-ready turns from evaluator review without
  deleting their transcripts, notes, or provenance; retain public browser
  automation as visible and explicitly labeled.
- [x] Publish prompt v33 after live eight-exchange QA exposed a ninth-turn
  conversation-recall rejection; keep unsourced model text only when it is
  grounded in the retained safe conversation, never as a site claim.

## Conversation-grounding release — 2026-08-31

- [x] Review all 29 current human production conversations without changing
  bucket placement; audit all 10 conversations longer than three turns.
- [x] Keep every successful or mixed-result transcript, note, annotation, and
  timestamp; hide only the three failed-only reports from the review queue.
- [x] Replace the short-follow-up classifier with bounded conversation-aware
  retrieval; the current release sends eight recent exchanges to the model.
- [x] Add regressions for Excel continuity, topic changes, support/page context,
  live calendar dates, failed-only cleanup, and the shared evaluator boundary.
- [x] Publish prompt v31 and preserve v30 as an immutable compiled artifact.
- [x] Update the dashboard Prompts preview to the exact v31 runtime policy
  before the subsequent v32 review.
- [x] Record team-source contributions and the action-item audit in
  `docs/TRANSCRIPT-AND-PROMPT-REVIEW-2026-08-31.md`.
- [x] Pass the complete local release suite: 359 Python, 29 frontend, and 18
  source-snapshot checks.
- [x] Push v31 directly to `zmuhls/fortune-digital-equity-guide-demo` main and
  deploy only the canonical zmuhls Railway production service.
- [x] Verify v31 health, model calls, Excel continuity, current calendar data,
  reset, cross-account shared-view digests, preserved review state, and an
  active unused one-time invitation for Jacob.
- [ ] Copy the exact modular v31 prompt into review-tagged Google Doc tabs. The
  existing System Prompt tab is populated, but Customization is still empty.
- [ ] Complete the remaining team-owned review work: the production audit
  proves four moves and one note by Sasha but no Maria categorization action;
  the connected Doc proves Maria review activity but does not independently
  attribute a prompt revision to Sasha.

## Human transcript review — 2026-08-21

- [x] Record the owner instruction to enable production human-conversation
  capture with a visible team-review notice and 90-day retention.
- [x] Make only privacy-clear `replica` and `wix` transcripts review-ready;
  exclude `benchmark`, `synthetic`, metadata, and direct API traffic.
- [x] Show stored dates, prompt-policy versions, and deployed app versions on
  conversation cards, transcript headers, and Guide messages.
- [x] Keep buckets, placements, notes, annotations, and Prompts shared across
  every evaluator account while retaining evaluator attribution.
- [x] Prove the migration and shared queue against PostgreSQL 17, including a
  database-enforced rejection when benchmark traffic is marked ready.
- [ ] Connect the production API to its private Postgres service, apply schema
  `009_human_review_capture`, and enable capture plus evaluation.
- [ ] Transfer evaluator identities and shared configuration from staging
  without copying benchmark transcripts, messages, sessions, or review rows.
- [ ] Push directly to `main`, verify the exact Pages and Railway deployments,
  and confirm a new benchmark smoke remains absent from the evaluator.
- [ ] Confirm the first future public `replica`/`wix` conversation appears
  newest-first for every evaluator. Earlier production message text cannot be
  recovered because capture was disabled when those requests occurred.

## Infobot model-first release — 2026-08-18

- [x] Remove the fixed conversational fallbacks that answered vague, frustrated,
  unsupported, or staff-routed requests without calling the model.
- [x] Require every successful non-private new turn to report
  `model_called: true`; keep the pre-model privacy hold as the sole successful
  zero-call exception.
- [x] Make vague requests use a model-authored clarifying question and make
  staff-bound requests use model-authored wording grounded in the current
  Contact record.
- [x] Reject malformed, unsupported, privacy-seeking, or twice-invalid model
  output as an operational error instead of fabricating a Guide message in the
  server, Pages client, or Wix element.
- [x] Preserve `model_called` in browser session state and mark rendered Guide
  turns with response provenance for Pages and Wix parity tests.
- [x] Move automated runs to the evaluator-hidden `benchmark` surface and make
  both release runners fail when any successful non-private turn skips the
  model.
- [x] Remove the forced clarification classifier and deterministic source
  collapse; the live model now chooses among bounded approved site records.
- [x] Incorporate the useful, fact-free guidance from the current team Infobot
  notes and core-setup review into prompt v21; preserve compiled v20 for review
  history and keep vendor-specific tooling, canned examples, unsupported crisis
  facts, and unconditional logging claims out of runtime.
- [x] Keep the visible shared dashboard tab and deployed catalog labeled
  **Prompts**, with v21 shown as the current immutable runtime policy.
- [x] Add a guarded staging transcript reset that preserves evaluator accounts,
  sessions, buckets, invitations, and all Prompts proposals/history.
- [x] Pass the complete local release suite: 297 Python, 28 browser-core, and 13
  snapshot tests.
- [x] Deploy the exact v21 commit to Railway staging and replay ordinary,
  frustrated, broad, specific, Spanish, and multi-turn requests with
  `model_called: true` on every non-private turn. The final ten-turn retrieval
  run completed 10/10, and Return produced a live model response in the browser.
- [x] Clear the existing staging transcript corpus in one guarded transaction,
  preserve evaluator and Prompts state, and prove automated benchmark traffic
  stays out of the reviewer queue. The reset removed 1,171 conversations,
  1,999 turns, and 3,784 messages while preserving four evaluator accounts,
  four bucket sets, 13 buckets, one active session, and the Prompts workspace.
- [x] Verify the staging browser, hidden benchmark capture, prompt provenance,
  aggregate privacy/integrity gate, and terminal deployment stability before
  promotion. Staging deployment `ad436f9b-8df8-4d78-a71f-1515ea4d4b6c` is
  terminal `SUCCESS`; the deployed runtime and prompt hashes match this tree;
  Return, model provenance, and page-to-page conversation persistence passed.
- [x] Merge through GitHub, verify Pages, then deploy the exact merged tree to
  Railway production with capture `none`, no database, and evaluation disabled.

## Meeting 4 release — 2026-08-17

- [x] Review Sasha's forwarded Meeting 4 summary and record bot, source,
  evaluator, prompt-review, persistence, and Wix interventions without copying
  private meeting credentials into the repository.
- [x] Refresh the public corpus atomically at Wix revision 2063: 138/138 live
  routes, 90 answer-authority pages, current `/workshops`, `/support`, device
  guidance, class descriptions, and the four current homepage/contact FAQs.
- [x] Archive the meaningful historical prompt releases, compile a dedicated
  current prompt, and add a bounded shared Prompts workspace whose proposals cannot
  activate runtime behavior.
- [x] Preserve the existing shared staging evaluator, timestamps, newest-first
  ordering, pagination, notes, annotations, buckets, and transcript integrity.
- [x] Run the frozen v11 staging gate: 41 scattershot cases plus 12 conversations
  and 50 turns. All 91 requests completed, all 51 factual answers called the
  model, and capture integrity passed; promotion correctly blocked on excessive
  one-choice clarification.
- [x] Preserve the failed v11 run as credential-redacted evidence and version
  the corrected evaluator overlay without changing its 41 cases, 12
  conversations, substantive grounding gates, or release-blocking outcome.
- [x] Redact continuation credentials from every tracked evaluation artifact,
  make both runners redact future artifacts, and rotate the staging token
  secret without deleting transcripts or evaluator records.
- [x] Implement the general v12 repair without factual response templates:
  exact-title and feature routing, FAQ/section evidence packing, relative-date
  routing, one-source `ASK` retry, concise direct-answer guidance, status
  caveats, and sentence-level repetition detection.
- [x] Vet `Infobot Notes_Fortune Society Digital Equity` from Documents and
  adopt only the added-value, fact-free prompt guidance: automated/non-staff
  identity plus plain, respectful, nonjudgmental language. Keep program facts,
  logging disclosures, unapproved crisis directions, and broad staff/language
  promises out of the system prompt.
- [x] Qualify the exact v16 commit on Railway staging: 41/41 scattershot cases,
  12/12 conversations, 50/50 turns, and 28/28 context-dependent turns, with
  every factual answer model-generated from an approved Fortune source.
- [x] Push the v16 branch, merge its release PR, verify the GitHub Pages workflow
  and public browser, then deploy that exact merged tree to Railway production
  with capture `none`, no database, and evaluation disabled.
- [x] Do not deploy or evaluate this guide on the CUNY PIT Lab website. PIT Lab
  may link to the canonical zmuhls production URL only if requested.

## Grounded generation and shared evaluation — 2026-08-17

- [x] Add **Start over** to the Pages and Wix clients; clear only tab-local turns, continuation credentials, and session storage without deleting captured evaluation data.
- [x] Replace the production model's page-ID-only contract with one reusable grounded-generation contract: one approved source ID plus a concise answer, or one clarifying question.
- [x] Send only the resolved question, approved excerpts, and relevant prior guide answer; keep raw participant history and excluded pages out of the provider prompt.
- [x] Reject unknown sources, invented numbers, external links, unsupported selections, malformed JSON, and answers without meaningful source overlap.
- [x] Allow alternate natural phrasings grounded in the same source, and route **What does the program offer?** to live generation instead of a canned branch.
- [x] Remove the remaining runtime factual fallback: without the live model, the guide abstains instead of extracting or serving a canned answer; source-mutation tests prove accepted factual output follows the approved record.
- [x] Make the evaluator queue, buckets, placements, notes, and annotations shared across all four authenticated evaluator accounts while preserving actor attribution in the audit log.
- [x] Keep existing captured conversations intact; the shared workspace uses the existing admin-owned bucket set and does not delete editor-specific legacy rows.
- [x] Timestamp conversation cards, transcript headers, and individual messages; sort every bucket newest first before paginating **Not yet reviewed**.
- [x] Serialize initial shared note, placement, and annotation writes so simultaneous evaluators receive a version conflict instead of silently overwriting one another.
- [x] Deploy the v11 candidate to Railway staging and verify model-backed answers,
  grounding rejection, browser reset, persistence after navigation, and the
  shared evaluator boundary; the formal quality gate found a release-blocking
  clarification defect now addressed by v12.
- [x] Promote only after the staging evidence is green; keep public production capture disabled.

## Superseded bounded source-selector baseline — 2026-08-12

- [x] Replace the prose-generating model contract with one reusable decision: return one allowed page ID or `ASK`.
- [x] Keep raw conversation history out of the provider request; send only the server-resolved question and bounded approved candidates.
- [x] Expand uncertain retrieval from three to ten candidates and prove all 144 substantive answer-authority pages are reachable by public title; exclude the Wix template-only Partners route.
- [x] Reject malformed IDs, unsupported distinctive terms, and model-selected records with no overlapping evidence; use compact clarification buttons instead of a fallback guess.
- [x] Remove Wix template people and boilerplate from searchable/model evidence while preserving legitimate structured team names from the About page.
- [x] Correct laptop guidance to match the live Devices page: free refurbished laptops through Computers 4 People have limited supply; the hold applies to mobile-device distribution.
- [x] Expand the stateful benchmark to 14 conversations and 59 turns, including Tech Fair Q&A and About/team deep-page retrieval.
- [x] Pass the full fixed and expanded suites against Railway staging with the real model: 41/41 fixed cases and 14/14 conversations, 59/59 turns, and 35/35 contextual turns. One truly ambiguous turn called the model and clarified; the synthetic-capture boundary stayed unchanged.
- [x] Promote the exact tested commit to production with capture `none`; repeat the capture-none benchmark at 41/41 fixed cases and 14/14 conversations, 59/59 turns, and 35/35 contextual turns; verify Return, a deep-page follow-up, clarification buttons, and source destinations in the published browser UI.

## Multi-turn retrieval release — 2026-08-12

- [x] Add 13 stateful retrieval conversations with 55 turns, including 32 deictic, elliptical, or topic-switch turns and a seven-turn stale-context test.
- [x] Freeze a pre-fix production baseline: 0/13 complete conversations, 28/55 turns, and 14/32 context-dependent turns passed.
- [x] Prefer reviewed class, device, certification, practice, support, registration, partner, impact, and Spanish-language sources without weakening privacy or source-authority gates.
- [x] Carry only the latest explicit safe topic into genuinely elliptical follow-ups; keep explicit topic shifts from reviving stale context.
- [x] Pass the real-model suite on Railway staging and production: 13/13 conversations, 55/55 turns, and 32/32 context-dependent turns on both.
- [x] Replay the seven-turn topic-switch conversation in the published browser UI with Return; all 14 visible user/guide messages rendered and the console stayed clean.
- [ ] Resolve the two stale pending turns reported by the staging aggregate audit before the next evaluator review. Do not alter participant-capture policy or copy staging data settings into production.

## Responsiveness and coverage release — 2026-08-12

- [x] Audit the canonical checkout, remote branches, and both GitHub repositories. The demo repository has no open PR; the PIT Lab mirror still has one open sync PR that predates this release.
- [x] Expand the fixed suite to 41 cases across all six request kinds and all four response kinds.
- [x] Fix typo routing, page-aware follow-ups, class clarification, prompt-injection cleanup, and the 600-character server boundary.
- [x] Route confident public-source matches without waiting for the model and remove model warmup from the send path.
- [x] Cap participant-facing answers at 32 words and keep clarifications, privacy holds, and staff handoffs shorter.
- [x] Deploy the exact tested commit to Railway staging and production; require terminal success and a capture-none production health boundary.
- [x] Merge the release PR, wait for GitHub Pages to finish, and verify the published asset hash.
- [x] Capture successful live runs for clarification, navigation, procedure, retrieval, privacy, and sensitive requests.
- [x] Re-run the immutable 41-case benchmark against production and attach its report to the release record.
- [x] Keep PIT Lab out of the deployment and evaluation path; use only the
  canonical zmuhls release URL.

## Released baseline — 2026-08-09

- [x] Release the minimalist Website Guide to Railway and GitHub Pages.
- [x] Route broad starter questions to the bounded choice set: **Take a class**, **Get a device**, or **Talk to staff**.
- [x] Keep public production conversation capture off (`capture_mode=none`); it has no chat database or evaluator access.
- [x] Prove the synthetic staging evaluator can persist review buckets, notes, and annotations.

## 1. Fortune review of public guidance

- [ ] Review the current public-source refresh with a Fortune source owner before treating changed material as approved guidance.
- [ ] Confirm the wording and destinations for the three starter choices, plus class, device, individual-support, registration, and staff-handoff questions.
- [ ] Record the approved source URL, owner, approval date, and next review date for any new public answer claim.
- [ ] Build a small staff-approved question set for regression testing. Generic questions should offer choices; specific questions should remain source-backed.

## 2. Keep the public release healthy

- [x] Perform a short post-release check: live `/health`, Return-key chat runs, starter buttons, and the responsive-layout contracts.
- [x] Review Railway operational logs for error rate and request metadata only. Do not inspect or retain participant chat text.
- [ ] On every future release, run `./run.sh test`, `python3 scripts/build_pages.py`, deploy, then verify the live artifact rather than relying on a successful build alone.

## 3. Synthetic evaluator — staging only

- [x] Add admin controls to issue or rotate a single-use, email-bound evaluator invite.
- [ ] Assign the Fortune representative and two student delegates only after Fortune names the three accounts.
- [x] Make each invite single-use, account-bound, and valid for 24 hours; keep raw tokens out of request paths, commits, variables, and logs.
- [ ] Deliver the named testers' links privately after Fortune supplies the account emails.
- [ ] Re-run the staging acceptance pass: save, reopen, and remove one note and annotation; confirm shared visibility from two evaluator accounts, a stale-version `409`, and `orphan_annotations=0`.

## 4. Wix pilot — blocked on the site owner account

- [ ] Create or open the private Wix app, generate its real app/extension IDs, and grant the required Secrets Manager and Members Area permissions.
- [ ] Add the bounded backend chat endpoint or explicitly configure the external Railway API; keep the provider key server-side.
- [ ] If the external API is used, allow only the exact Fortune production and approved preview origins.
- [ ] Install first on a Wix test site and verify page context, compact controls, Return/Shift+Return, mobile layout, privacy hold, and staff handoff.
- [ ] Obtain Fortune approval before enabling the guide on the production Wix site.

## 5. Participant capture approval

- [x] The August 21, 2026 owner instruction authorizes production capture for
  the shared human-conversation review pilot.
- [x] Keep production and staging databases separate; migrate evaluator
  identity/configuration only and never copy benchmark transcript rows.
- [x] Keep capture mode out of the compact participant interface while retaining
  the pre-model privacy hold, a 90-day retention period, private database
  networking, and human-only queue eligibility.
- [x] Deploy schema `009_human_review_capture` to production, connect the
  private production database, and verify capture/evaluation readiness.
- [x] Preserve the three claimed evaluator accounts and shared 13-bucket
  configuration without copying sessions, staging transcripts, or tests.
- [x] Verify a production benchmark capture stays outside the evaluator and a
  blocked privacy turn stores no message text.
- [ ] Record any later change to retention, reviewer identities, export/deletion
  procedure, incident ownership, or pilot end/renewal date before changing the
  production configuration.

## Reference

- [Conversation capture contract](deployment/CONVERSATION-CAPTURE.md)
- [Evaluation workspace contract](deployment/EVALUATION-WORKSPACE.md)
- [Wix adoption roadmap](deployment/wix/ROADMAP.md)
- [GitHub Pages roadmap](deployment/github-pages/ROADMAP.md)
