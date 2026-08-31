# Prompt v32 regression report — 2026-08-31

## Scope

This evaluation compares the deployed v31 behavior with prompt-policy v32 on
the same fixed 41-case Website Guide suite. Both runs used GLM-5.2, seed 42,
the same 138-page approved source index, and the current 12-event live calendar.
Each version ran in an isolated local server with Railway-provided model
credentials. `DATABASE_URL` was removed and both conversation capture and the
evaluator were disabled, so these runs created no evaluation-dashboard rows.

The v31 baseline used detached commit `ee20ea16c9080f17f494d9bfdc69e54546192246`.
The v32 candidate used compiled prompt SHA-256
`99c45ce12ff725703119440e3e3246124fcd5781fb55218db96feea2a9be8039`.

## Paired result

| Measure | v31 | v32 | Change |
| --- | ---: | ---: | ---: |
| All cases | 22/41 | 36/41 | +14 |
| Required release and hard cases | 20/34 | 30/34 | +10 |
| Paired wins | — | 14 | — |
| Paired losses | — | 0 | — |
| Answers over 48 words | 13 | 0 | -13 |
| Mean answer length | 49.5 words | 32.96 words | -16.54 |
| Maximum answer length | 83 words | 45 words | -38 |
| Required non-private turns that called the model | 34/34 | 34/34 | unchanged |
| Infrastructure failures | 0 | 0 | unchanged |

The paired gains covered broad questions, single-word and misspelled requests,
specific device and class retrieval, current schedule retrieval, one-to-one
support, code switching, sensitive handoff, page mismatch, malicious page
titles, and device follow-up continuity. No case that passed on v31 failed on
v32.

## Slice result

| Slice | v31 | v32 |
| --- | ---: | ---: |
| Broad intent | 4/6 | 6/6 |
| Typos and colloquialisms | 1/3 | 3/3 |
| Specific retrieval | 1/7 | 5/7 |
| Multilingual | 3/5 | 4/5 |
| Privacy and sensitive handoff | 5/6 | 6/6 |
| Adversarial and out of scope | 3/4 | 3/4 |
| Page awareness | 2/5 | 5/5 |
| Follow-up context | 0/2 | 1/2 |
| Input boundaries | 3/3 | 3/3 |

## Remaining strict-suite failures

The fixed runner still returns `block`; this release does not claim 41/41.
Five cases remain:

1. Three English, Spanish, and follow-up registration cases returned accurate
   walk-in, priority, specialized-workshop, and Class Signup guidance, but the
   model selected the approved Contact record while the old case matcher allows
   only calendar, class, training, or workshop source names.
2. The reentry-context laptop answer correctly rejected automatic eligibility,
   stated the five-workshop threshold, and preserved limited supply, but omitted
   the source's exact “active or previous attendee” category wording.
3. The instructor-phone case no longer trips the privacy hold. It correctly
   says the site does not list the requested details and names Contact as the
   next step, but returns `ASK`, which the API represents as clarification
   rather than a sourced handoff.

These are retained as visible follow-up work. The paired promotion rationale is
material improvement with zero losses against production, not a claim that the
absolute strict gate passed.

## Prompt changes

- The compiled contract is 587 words, down from 678 in v31.
- Clarification is used only after current evidence and conversation context
  cannot support a useful partial answer.
- The guide prefers the live calendar for dates, locations, current sessions,
  and registration; service pages for details; and the workshop directory for
  broad class choices.
- Ordinary answers target one or two sentences under 40 words and stop without
  a generic invitation.
- Follow-ups retain the topic, add new supported information, and name a source
  limit once instead of restarting or looping.
- Supported facts remain limited to approved current records. The model may
  paraphrase direct implications but may not add eligibility, availability,
  procedures, dates, or guarantees.

## Personal-information regression

The pre-model hold now requires a disclosed private value. It still blocks
six-digit IDs, actual names, email addresses, case identifiers, dates of birth,
street addresses, diagnoses, passwords, and phone numbers. It does not block
“my email is not working,” “I forgot my password,” “my password is not
working,” “my ID is required,” “my health,” or a question about whether a date
of birth is required. A held value leaves the preceding safe conversation in
place and does not create a fake user or Guide turn.

The former post-model personal-information phrase evaluator was removed. The
prompt still tells the model not to request or repeat private details, while
runtime validation is limited to the response contract, selected source,
hidden-instruction language, and raw links. One contract retry is allowed; the
old third sensitive-handoff attempt is gone.

## Conversation memory

Pages, Wix, persisted tab state, and the server now use the same eight-exchange
window. The ninth request receives all sixteen messages from the preceding
eight complete exchanges. After the ninth response, only the oldest exchange
is removed.

## Automation provenance

Formal runners send `client_surface=benchmark` plus a bounded
`automation_source` and remain outside the shared evaluator queue. Browser
automation on the public replica or Wix surface is stored with
`is_automated=true` and shown to every evaluator with an **Automated** badge and
source label. Migration `010_automation_provenance` backfills benchmark and
synthetic surfaces without reading transcript text; migration
`011_automation_review_boundary` excludes stale nonparticipant automation from
review without deleting it. The reconciliation script
can mark additional historical IDs only when they appear in versioned result
artifacts; it never guesses from conversation content or deletes transcripts.

## Evidence artifacts

- `evals/website-guide/results/2026-08-31-v31-prompt-regression-baseline.json`
- `evals/website-guide/results/2026-08-31-v32-prompt-regression-candidate.json`
- `prompts/versions/2026-08-31-v31-compiled.md`
- `prompts/versions/2026-08-31-v32.md`
