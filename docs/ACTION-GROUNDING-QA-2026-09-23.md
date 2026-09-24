# Source actions, conversations, and review UI — September 23, 2026

## Defects reproduced and repaired

A scoped read-only review of the eight latest human evaluation conversations
confirmed the reported Calendar-to-Contact button. The answer selected Calendar,
but the page-scoped frontend preferred a keyword-generated related link called
“Registration details.” Both the Pages and Wix clients now retain the model's
selected source, including when it is the active page. The guessed related-link
table is removed; related destinations come from labeled source-page anchors.

Multi-turn replays also reproduced factual replies losing their source selection.
The gateway now requests JSON-object output, not strict JSON Schema. There is
still one generation per accepted turn, with native conversation history; no
classifier, canned answer, repair generation, or provider switch was added.

When a follow-up names only “that,” an exact class title in the last guide answer
can retrieve its actual indexed page. Assistant text is a lookup cue, never factual
evidence. This repaired a real registration → tomorrow → “what's that about”
sequence that previously missed the published AI capstone description.

Calendar evidence retains signup instructions and labeled locations alongside
dated events. A single New York clock supplies computed session status. Ended
events are excluded from generic upcoming requests; explicit date/full-month
questions can still retrieve them. Missing end times remain unknown.

## Prompt and shared dashboard

Prompts now edits one complete system prompt, replacing rather than appending
another complete prompt. The expandable runtime view shows exactly that saved
prompt. Historical module proposals remain available but are explicitly inactive.
The v1.37 prompt uses actual signup destinations, keeps program-specific rules
together, and acknowledges conflicting requested details.

Migration 015 preserves the previous prompt body and all append-only revision
history, then activates v1.37. It does not modify conversations, messages,
evaluations, buckets, accounts, or sessions. A disposable PostgreSQL 17 migration
test preserved seeded data and prior revision bodies; rerunning the migration
runner was a no-op, and history-update protection remained enabled.

Every conversation card now has an always-visible, highlighted “View transcript”
button outside its collapsed details. Desktop/mobile preview checks covered
opening/closing transcripts, pagination, and prompt save/readback. No real shared
prompt was changed during those preview tests.

## v1.37 candidate verification

| Check | Result |
| --- | --- |
| Python regression suite | 423 passed |
| Frontend contract tests | 33 passed |
| Snapshot generator tests | 18 passed |
| Generated routes | 150 |
| Local links/resources and fragments | 4,130 validated |
| Preserved official live actions | 161 |
| Indexed main-content action links | 577 |
| Live model journeys | 10 journeys, 26 turns; all model-called/source/capture checks passed |
| Latency in this concurrent replay | median 4.605 s; maximum 12.08 s |

The live replay used the production GLM-5.3-Flash provider configuration in the
local release candidate, with an empty DATABASE_URL and capture_mode=none. The
script refuses servers with transcript capture enabled. These tests did not
create evaluation transcripts. Production host checks are separate from this
live-provider test.

The first live replay failed repeatedly despite green unit tests. JSON-object
mode fixed lost sources; independent semantic review then caught past sessions,
missing class descriptions, and conflated tutoring hours. Those issues were
fixed before the final replay. Its complete synthetic transcripts are in
[the final replay](../evals/website-guide/results/2026-09-24-action-journeys-v37.json).
The tomorrow/capstone assertions are specific to the September 23 test date.

An independent source review found no release-blocking factual/action errors in
the final 26 replies. Two precision improvements remain: “next regular class” is
clearer than “next listed session” when open lab starts earlier, and an open-lab
follow-up could state 1:30 PM explicitly rather than only “before class.”

### Production retest and v1.38 correction

The v1.37 production-container retest exposed a failure missed by that candidate
run: the answer told the visitor to register on Calendar, but selected Contact as
its factual source. A citation and an action destination cannot safely be treated
as the same field.

The v1.38 model response therefore keeps `pick` for evidence and adds `action_url`
for its chosen next step. The server accepts only exact candidate URLs or labeled
links supplied in the evidence. The browser displays that validated action before
the citation fallback, preserving it across pages. There is no keyword route table,
action classifier, hardcoded answer, or extra model request. Invalid action URLs
cannot become executable links and do not suppress an otherwise valid answer.

The expanded checker tests source and button independently: 11 journeys, 30 turns,
with 13 explicit button checks. Its first run passed those structural checks but
semantic review caught a missing walk-in FAQ and omitted email-class availability.
The excerpt filter had retained only one FAQ question, dropping equally relevant
answers. It now ranks intact question/answer pairs and retains relevant pairs
within the existing content budget. The prompt also carries program availability
limits into next-step guidance.

Migration 016 appends and activates v1.38 while preserving v1.37 and all earlier
revisions. No conversation, review, or account data is changed.

The final v1.38 run passed **429 Python, 34 frontend, and 18 snapshot tests**.
All **30 live turns and 13 explicit button checks** passed. Mean latency was
3.99 seconds, with a 10.36-second maximum. See the
[complete v1.38 release replay](../evals/website-guide/results/2026-09-24-action-journeys-v38-release.json).
The earlier failed candidate is retained separately, with its semantic findings.

Independent review confirmed correct registration buttons even when Contact is
the factual citation, the restored walk-in FAQ, current dates, and the actual
capstone description. It also identified remaining conversational limitations:

- Tutoring hours match the September PDF (10–11:30), but one answer does not
  acknowledge the conflicting 10:30–12 schedule on the support page. The answer
  is supported by one current source; the source conflict remains unresolved.
- “No appointment is mentioned” is weaker than the source's explicit statement
  that no scheduling is necessary for Open Computer Lab.
- Email signup advice correctly says to check available calendar dates without
  promising a bookable session, but could mention the service page's availability
  limitation more explicitly.

These are not represented as a perfect semantic pass. No model-output rewriting
or canned responses were added to conceal them. Calendar evidence now labels
class hours/address separately from support programs to avoid transferring a
classroom address to an appointment service.

A real browser run at 375 px clicked the generated “Go to Digital Equity calendar”
button and reached the local /calendar/ route, not Contact. Menu typography and
dropdown positioning were checked at desktop and intermediate widths. Calendar
week/day/filter controls were checked at 375, 768, and 1280 px, with no horizontal
overflow and at least 44 px calendar controls. Native dropdown selection was
verified programmatically; keyboard day controls were verified.

## Source fidelity and limits

See [the full mirror/link audit](MIRROR-LINK-AUDIT-2026-09-23.md). All 150 currently
published official routes match the mirror inventory and Wix revision 2090.
The index and rendered mirror use the same manifest-bound captures.

This is not a claim of a perfect Wix runtime clone. Registration explicitly
hands off to Fortune's live calendar; users must choose their session there.
Captured availability is not live, so stale spot counts were removed. Beyond the
captured schedule, the calendar shows a clear live-calendar link rather than
inventing sessions. Four broken external destinations also exist on the original
site and have not been replaced with guesses. Full mobile reflow of every source
page is not established by the focused menu/calendar/guide checks above.

## Data-preservation release gate

Before deployment, a read-only digest covered all 670 message bodies created
before 2026-09-24 02:43 UTC: `9caf245b0ce82615ac5404e790171303`.
The shared prompt was edit 36/version 4 with four historical revisions. Post-release
verification must reproduce the message digest, retain those historical bodies,
and confirm that the saved prompt, runtime prompt, and dashboard compilation agree.
No transcript reset or evaluator-data deletion is part of this release.
