# Production transcript and prompt review — 2026-08-31

## Scope and evidence boundary

This review covers the shared production evaluation store, every currently
reviewable human conversation, all conversations longer than three turns, the
v31 Website Guide prompt, and the current public Digital Equity source mirror.
Benchmark conversations were excluded from the evaluation queue. Transcript
text was inspected only in memory to derive quality signals; this report does
not reproduce participant messages, account emails, credentials, database
addresses, or raw conversation and turn identifiers.

No reviewer placement, note, annotation, timestamp, successful turn, or mixed
success/failure conversation was deleted or reordered. Three conversations
containing only failed attempts are retained in the database for audit but no
longer extend the human-review queue.

## Production population reviewed

| Measure | Result before the v31 release |
| --- | ---: |
| Stored conversations in scope | 71 |
| Stored turns | 139 |
| Stored messages | 251 |
| Human production conversations | 29 |
| Benchmark conversations | 42 |
| Human conversations with at least one complete turn | 24 |
| Visible cards before failed-only cleanup | 27 |
| Failed-only cards retained but hidden | 3 |
| Conversations longer than three turns | 10 |
| Failed attempts within human conversations | 8 |
| Shared bucket placements | 11 |
| Shared message annotations | 2 |
| Privacy, referential-integrity, or orphan-message findings | 0 |

The latest human turn in the audited snapshot was recorded on August 31,
2026. Stored timestamps and prompt/build versions remain attached to each turn,
and the dashboard orders the shared queue by newest activity before paginating
it.

## Long-conversation review

All ten conversations longer than three turns were reviewed. They fall into
the following patterns:

1. A six-turn conversation contained three complete clarifications followed by
   three usage-limit failures. It never progressed to useful page evidence.
2. A six-turn device-support conversation began on Contact and needed two
   clarifications before reaching the Device material. The active page had too
   much influence and recovery was slow.
3. An eleven-turn mixed-topic conversation successfully changed subjects six
   times, but some answers were longer than needed and one support turn selected
   Calendar evidence.
4. The Excel conversation selected Excel initially, then produced five
   source-free clarifications. The original Excel topic was absent from the
   model's later retrieval context.
5. A seven-turn conversation produced six consecutive source-free
   clarifications before finally reaching Excel evidence.
6. Another seven-turn conversation contained a 92-word answer, four
   clarification turns, and a near-repeat. This combined verbosity with a
   clarification loop.
7. An eleven-turn legacy conversation included a rejected model response,
   source-free clarifications, and a class follow-up routed to Calendar.
8. A four-turn device conversation was correct for three turns, then a short
   follow-up lost the device topic and moved to Contact.
9. A six-turn legacy conversation contained one rejected response, ended with
   three clarifications, and included a near-repeat.
10. A five-turn older navigation conversation moved among several pages and
    ended on Support. It was broadly successful but less concise than the
    current contract.

The failures were not caused by one bad canned paragraph. They were systemic:
the server sent only the immediately previous Guide answer to the model, while
a narrow referent matcher decided whether an older participant topic could be
used for retrieval. Once that matcher missed a short follow-up, the model saw
weak or unrelated evidence and either clarified again or drifted to another
page.

## v31 remediation

Prompt policy `2026-08-31-v31`, behavior release
`digital-equity-conversation-grounding`, makes the following changes:

- every valid, non-private new turn still calls the language model; no factual
  or conversational response is assembled from a canned response table;
- Pages and Wix send up to six recent exchanges, rather than three;
- retrieval searches the current question and recent participant turns without
  a follow-up intent/referent classifier;
- model-authored answers are excluded from search ranking so a weak answer
  cannot reinforce its own source drift;
- explicit current source titles and evidence routes retain priority over broad
  directory pages;
- the complete bounded conversation is supplied to the model so short phrases
  such as “what else?” and “what about that?” can retain their subject;
- the model searches approved records from across the current Digital Equity
  site before asking one necessary clarification, and it is told never to
  repeat the same clarification;
- a calendar request containing an explicit past date and year now correctly
  returns events after that date instead of silently moving the date into the
  following year;
- the dashboard excludes failed-only reports while keeping mixed failures
  visible with their successful context;
- the Prompts dashboard shows the exact v31 compiled prompt and current module
  variants.

The remaining deterministic participant copy is the pre-model personal-data
hold. It exists to keep personal details out of the model and transcript store;
it does not answer a Digital Equity question. Network and service errors also
remain UI status, not Guide dialogue.

## Purpose and team contributions reflected in the prompt

The v31 prompt defines the Website Guide as an informational guide to the
Digital Equity site. It can explain and navigate public site material, but it
cannot enroll or book a participant, access an account, process a request,
decide eligibility, provide case management, act as a tutor, or replace a
person. When a human action is required, it explains the public next step from
the selected source.

| Source contribution | Added value carried into v31 |
| --- | --- |
| Jacob's Wix notes | Current workshop descriptions and FAQs; clear treatment of staging, inactive, outdated, and menu-hidden content; emphasis on source freshness. |
| Maria and Sasha's working notes | Broad site context, service-versus-resource distinctions, FAQ use, first-contact navigation, clarification only when necessary, and confident but grounded participant-facing language. |
| Digital Equity strategy material | A nonjudgmental entry point that increases participant agency without replacing human support; current information, staff/participant testing, dignity, trust, and privacy. |
| Infobot system-prompt draft | Clear AI identity, short practical explanations, grounding, and role limits. Rigid crisis scripts and claims about logging were not imported because they do not belong in this public informational contract. |

The public source mirror contains 138 reviewed routes from Wix revision 2063,
including the four current home/contact FAQs and the revised workshop catalog.
Inactive, staging, archive, member, and navigation-only pages cannot support a
factual answer. The calendar route refreshes the public downloadable calendar
at runtime and retains the rendered page schedule as a bounded fallback.

## Action-item ledger

| Action item | Status | Evidence and remaining boundary |
| --- | --- | --- |
| Fix shared evaluation view | Complete and production-verified | All three claimed accounts return the same 24 conversations in the same recency order, with identical bucket and placement digests. Eleven placements, two notes, and two annotations remain stored. |
| Collate the compartmentalized system prompt into Google Doc tabs, tagged for review | Partial | The team Doc has nine tabs and a populated System Prompt tab, but the Customization tab is effectively empty and the modules are not yet separated into review-tagged tabs. This repository now provides the exact v31 modular source for that work. |
| Document all prompt versions with change history | Complete in repository | The hash-verified manifest preserves every artifact through v31; v30 was frozen into its own compiled artifact before current.md advanced. |
| Remove canned responses and restore dynamic model behavior | Complete and production-verified | Ten ordinary, identity, calendar, device, and multi-turn Excel requests all called the live model under v31. Tests reject the former canned strings, lexical response gate, and deterministic factual builders. |
| Update the mock site for Jacob's Wix changes | Complete for the reviewed source release | The mirror contains the current four FAQs, revised workshop descriptions, 138 routes, and Wix revision 2063 provenance. |
| Add reset/restart | Complete and browser-verified | Pages and Wix expose Start over, clear only local conversation state, and preserve unrelated site state. The production browser cleared the visible turn and returned to the opening controls. |
| Add transcript timestamps | Complete | Turn/message timestamps and prompt/build versions are stored and shown; queue ordering is newest-first. |
| Maria and Sasha categorize transcripts | Partial | Production attribution records four moves and one saved note by Sasha. No Maria categorization action is present in the current audit. Existing placement was not changed by this review. |
| Maria and Sasha refine the shared prompt Doc | Partial | Maria-authored review activity is visible in the connected Doc. Sasha's individual contribution cannot be independently attributed from the available connector history, although shared team material is present. |

## Verification gates

The pre-deployment suite passed:

- 359 Python tests, including evaluation API, persistence, privacy, prompt
  provenance, source retrieval, calendar, and multi-turn regression coverage;
- 29 frontend tests, including model provenance, Enter-to-send, reset,
  six-exchange context, persistence across navigation, and removal of the former
  review/privacy banner;
- 18 deterministic source-snapshot tests.

Production acceptance passed:

- health reports prompt v31, a ready live model, 138 indexed pages, a live
  calendar refresh with 12 structured events, and ready transcript/evaluation
  storage;
- ten benchmark-only live requests returned HTTP 200, called the model on
  every turn, used v31, and contained none of the former fallback copy;
- a six-turn Excel conversation retained the same class evidence throughout,
  advanced without an exact repeated answer, and ended with the correct site
  route;
- Return submitted in the production browser, Start over cleared the local
  conversation, and a new conversation persisted while moving from Home to
  About;
- every browser and scripted QA conversation was retained as benchmark data
  and excluded from the human review queue;
- all three claimed evaluator accounts returned the same 24-conversation
  digest and preserved 11 placements, two notes, and two annotations;
- the replacement editor-three invitation for Jacob is active, unused, and
  email-bound;
- the runtime now prefers the explicit release version over stale Railway Git
  metadata, so new transcript cards can be associated with the actual release;
- provider output is capped at 256 tokens and privacy-safe timing logs record
  only duration and token counts, allowing latency outliers to be diagnosed
  without storing prompt or response text in application logs.
