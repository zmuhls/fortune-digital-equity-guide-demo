# Conversation capture and evaluation deployment

This is the operator contract for PostgreSQL evidence capture and the evaluator dashboard. Automated benchmark traffic remains isolated from the public production guide's human-conversation review queue.

## Current implementation slice

The first migration creates `conversations`, `conversation_turns`, and `conversation_messages`; later migrations add server-approved page context, evaluator data, bounded interaction labels, and the human-only review gate. Each turn records opening or follow-up stage, request type, request and response language, retrieval scope, app version, and prompt-policy version. Every accepted chat response receives UUIDs for its conversation, turn, client event, user message, and assistant message. Clients keep a server-signed conversation continuation token and reuse one client event ID across retries.

The server reserves a turn before generating an answer and completes storage before returning it. If capture is required and PostgreSQL is unavailable, `/health` and `/api/chat` return `503` rather than creating an unlogged conversation. Request limits, a 50-turn conversation bound, request fingerprints, and expiring turn leases limit duplicate or unbounded writes. The server stores no IP address, user agent, device identifier, or provider credential.

Capture modes are explicit:

- `none`: default; no PostgreSQL conversation rows and no transcript database requirement.
- `metadata`: IDs, page record, routing/result fields, model state, timing, and privacy/review state; no question or answer text.
- `transcript`: the metadata above plus question and answer text only for turns classified `clear` by the automated privacy hold. Blocked or sensitive turns contain no message rows.

`transcript` is not an anonymization guarantee. Fortune approved production capture for the shared human-conversation review pilot on August 21, 2026. Capture mode must not inject status or review copy into the compact participant interface. Only privacy-clear conversations from the public `replica` and `wix` surfaces become review-ready. Automated `benchmark`, legacy `synthetic`, and direct API traffic never enter the evaluator queue.

Captured conversations receive `expires_at`; the server purges expired conversations at startup and at most hourly while serving capture traffic. The default is 90 days and can be shortened for a pilot.

## Railway layout

Use one existing project with isolated environments:

```text
fortune-guide-demo
├── production
│   ├── guide-api        human transcript capture and shared evaluator
│   └── Postgres         private capture/evaluation database
└── staging
    ├── guide-api        evaluation API; synthetic traffic only
    └── Postgres         private DATABASE_URL reference
```

Each environment uses its own private PostgreSQL service. The API receives `DATABASE_URL=${{Postgres.DATABASE_URL}}`; the database password never belongs in Git, a browser bundle, or a command transcript. Generate the conversation-token and evaluator-auth secrets independently and pipe them to Railway through standard input. Never point production at the staging database or copy benchmark transcripts into production.

Required capture variables:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
FORTUNE_APP_VERSION=<deployed Git commit SHA>
FORTUNE_CONVERSATION_CAPTURE=transcript
FORTUNE_CONVERSATION_TOKEN_SECRET=<at least 32 random characters>
FORTUNE_CONVERSATION_RETENTION_DAYS=90
FORTUNE_TURN_LEASE_SECONDS=180
FORTUNE_MAX_TURNS_PER_CONVERSATION=50
FORTUNE_CHAT_REQUESTS_PER_HOUR=120
FORTUNE_CHAT_REQUESTS_PER_DAY=2000
```

The prompt-policy version is code-owned in `prompt_policy.py` and is passed to
the recorder by the server. Do not set a separate Railway prompt-version
variable; that previously allowed transcript metadata to drift from the prompt
that actually generated the response.

`railway.json` runs `python3 scripts/migrate.py` as a pre-deploy command and starts `python3 server.py`. Set variables on the intended environment explicitly; never copy a staging database reference or staging-only rate limits into production.

## Staging acceptance

A staging release is accepted only after all of these are true:

1. The local Python and JavaScript suites pass.
2. The Railway deployment reaches terminal `SUCCESS`.
3. `/health` returns `200`, `capture_mode=transcript`, `database_ready=true`, `enabled=true`, and `schema_version=009_human_review_capture` without revealing database or token values.
4. Re-running the migration reports a current schema.
5. One invented `benchmark` question produces stable UUIDs and exactly two message rows while remaining review-pending and absent from the evaluator.
6. Replaying its client event ID returns the same turn and response without another row.
7. Reusing that event ID with different input returns `409`.
8. A synthetic six-digit-ID sentinel produces an excluded turn and zero message rows; the sentinel is absent from all persisted JSON and text fields.
9. Human `replica`/`wix` turns are review-ready; `benchmark`, `synthetic`, and direct API turns are not.
10. `python3 scripts/audit_conversation_quality.py` passes without selecting message content.

## Dashboard migrations that come next

Do not combine evaluator identity and taxonomy tables into the evidence-capture migration. Add them after staging capture is proven:

1. Identity and sessions: four invited users, one `admin` and three `editor` roles; Argon2id password hashes; single-use invite/setup tokens stored only as hashes; short-lived, rotated, HttpOnly/Secure/SameSite cookies; CSRF protection; login throttling; session revocation; no shared credentials.
2. Taxonomy: versioned bucket sets, standard seed buckets, editor-created buckets, ordering and color metadata, archived states, and ownership.
3. Evaluation: one current placement per conversation and bucket set, optional structured notes, optimistic version numbers for drag-and-drop conflicts, and an append-only audit event for every create, rename, reorder, place, remove, and archive action.
4. Admin synthesis: filter and aggregate through a role-checked API. Editors receive only approved review-ready conversations; privacy-excluded turns never enter the transcript queue. CSV or report export must be an explicit admin action with audit logging.

Seed the four accounts with expiring invitation links rather than checked-in passwords or Railway variables. The admin may manage bucket templates and accounts; all four reviewers can sort conversations and create personal or shared buckets according to the approved policy.

## Production approval record

The August 21, 2026 owner instruction authorizes the production human-conversation review pilot, the shared evaluator workspace, and a reset baseline with no imported benchmark transcripts. The release uses the exact visible notice above, a 90-day retention period, private Railway networking, four bounded evaluator slots, and human-only queue eligibility. Blocked or sensitive turns store no message text. A later change to retention, reviewer identities, inclusion rules, export/deletion procedure, or pilot duration requires a new explicit owner decision and a documented deployment change.
