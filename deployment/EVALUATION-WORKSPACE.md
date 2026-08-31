# Evaluation workspace deployment contract

The evaluator is a Railway-only surface at `/evaluation`. It is not copied into the GitHub Pages artifact and it does not reuse Fortune's public member login.

## Initial account state

Migration `003_evaluator_identity.sql` creates exactly four inert slots:

- `admin`: one Fortune representative
- `editor-1`: student delegate
- `editor-2`: student delegate
- `editor-3`: reserved editor slot

All four begin with null email, display name, password hash, invitation digest, and claim time. Deployment must not generate invitations automatically. An operator issues the first admin invitation later from a private Railway shell:

```bash
python3 scripts/issue_evaluator_invite.py admin \
  --base-url https://<staging-domain>
```

The command prints one fragment-based claim link. PostgreSQL stores only its keyed digest. Do not place the raw link in Railway variables or logs.

After the admin account is claimed, the administrator can open **Account** in the evaluator and create or replace an email-bound link for any unassigned editor slot. Each link opens the first-use registration form, expires after 24 hours, works once, and signs the tester in immediately after registration. Share links only through a private channel; do not paste them into issues, commits, deployment logs, or test reports.

Returning testers sign in at `/evaluation` with the email and password they chose during registration. All four accounts open the same shared queue, buckets, placements, conversation notes, and message annotations. Changes persist in PostgreSQL after reload, sign-out, and a new browser session; audit records identify the evaluator who made each change. Interface preferences remain browser-local.

If a claimed account must be reassigned, use the private operator command below. It revokes active sessions and clears only that slot's authentication fields; the shared workspace and audit history remain intact.

```bash
python3 scripts/reset_evaluator_invite.py admin \
  --confirm-reset admin \
  --base-url https://<staging-domain>
```

The replacement link follows the same single-use, 24-hour, private-delivery rules. Resetting a claimed account is destructive to its existing login and requires explicit owner authorization.

## Evaluator variables

```text
FORTUNE_EVALUATION_ENABLED=1
FORTUNE_EVALUATOR_AUTH_SECRET=<independent random value of at least 32 characters>
FORTUNE_EVALUATOR_IDLE_SECONDS=1800
FORTUNE_EVALUATOR_ABSOLUTE_SECONDS=28800
FORTUNE_EVALUATOR_INVITE_SECONDS=86400
FORTUNE_EVALUATOR_MIN_INACTIVE_SECONDS=60
```

`DATABASE_URL` must continue to use Railway private networking. The evaluator secret must not reuse the conversation continuation secret.

## Data boundary

Only a conversation satisfying every condition enters the shared review queue:

- transcript capture mode;
- public human surface: `client_surface IN ('replica', 'wix')`;
- unexpired and inactive for the configured minimum;
- at least one privacy-clear completed turn or failed attempt;
- exactly one user and one assistant message for each completed turn;
- zero or one visitor message for a failed attempt, with no assistant message.

Clear failed attempts remain visible so a model or service failure cannot silently remove a human submission from review. Earlier failed attempts may have metadata only because prior releases did not retain their visitor message. New failed attempts retain the privacy-clear visitor question without an invented assistant response.

Privacy-held turns are withheld in full; their presence does not suppress other visible turns in the same conversation. Automated `benchmark`, legacy `synthetic`, and direct API conversations remain review-pending and cannot satisfy the queue gate. Browser automation on a public `replica` or `wix` surface remains in the shared queue with an **Automated** badge and bounded source label. Every authenticated evaluator receives the same placements, buckets, conversation notes, and message annotations. Cards identify the number of turns grouped into each browser conversation, show failed-attempt counts, and refresh periodically from PostgreSQL. Cards and transcript details show the stored date, prompt-policy version, app version, and automation provenance when present. Annotation rows reference canonical message IDs and never copy transcript text. Mutations retain evaluator attribution in the append-only audit log. All evaluation records cascade away when the conversation expires.

Automated suites and capture verification use `client_surface='benchmark'`
and an explicit `automation_source`. Those rows remain available to aggregate
audits but never satisfy the shared review-queue gate. Public browser harnesses
set `automation_source='browser-webdriver'`. Use
`scripts/reconcile_automation_provenance.py` to flag only conversation IDs in
versioned evaluation artifacts; it is dry-run by default and never reads or
deletes transcript content.

The shared review taxonomy includes **Success**, **Needs work**, the virtual **Not yet reviewed** area, and custom buckets. Migration `008_remove_handoff_bucket.sql` returns old Handoff placements to Not yet reviewed and archives the old bucket rows without deleting their history.

## Prompts boundary

All authenticated evaluators share one Prompts workspace. Editors and the administrator can create or revise draft suggestions and add comments for four presentation modules only: style, clarification, page awareness, and follow-up behavior. The administrator can mark a proposal ready for code review or archive it.

Prompts never edits the compiled system prompt and has no activation or publishing route. Grounding, approved source access, privacy, safety, response validation, language handling, and deployment remain code-controlled. A ready proposal becomes live only after a developer converts it into an allowlisted prompt version, reviews it through Git, and deploys that code.

## HTTP boundary

- Sessions store only HMAC digests and use `__Host-fs_eval` with `Secure`, `HttpOnly`, and `SameSite=Strict`.
- Mutations require a same-origin browser request and a session-derived CSRF token.
- Editors cannot read account administration routes.
- Evaluation pages use `frame-ancestors 'none'` and are no-index.
- Repository source, migrations, tests, environment templates, and snapshots are not served by the backend.
- Structured request logs contain request ID, method, route template, status, and duration only—never transcript text, notes, email, cookies, tokens, or persistent IP identifiers.

## Release gate

1. Run `./run.sh test` and both snapshot checks.
2. Apply migrations through Railway's pre-deploy command.
3. Confirm `/health` reports evaluation schema `010_automation_provenance`, four total slots, and the expected claimed/unassigned slot counts.
4. Confirm `/server.py`, `/.env.example`, `/migrations/003_evaluator_identity.sql`, `/migrations/009_human_review_capture.sql`, and `/scripts/issue_evaluator_invite.py` return `404`.
5. Confirm `/evaluation` shows the login surface and no reviewer data without a session.
6. Claim the admin account, create one editor link from **Account**, and verify first-use registration signs the editor in without exposing the token in an HTTP request path or server log.
7. Save a bucket placement, note, and annotation as one evaluator; sign in as another evaluator and confirm the same state is visible. Make a second change and confirm the first evaluator sees it after reload. Confirm both users see the same newest-first human transcript set, timestamps, prompt versions, and app versions.
8. Create, revise, and comment on one Prompts proposal as an editor; confirm another evaluator sees it, confirm an editor cannot change its status, and confirm the administrator can mark it ready without activating it.
9. Confirm the same invitation cannot be claimed twice, then leave the remaining invitation fields null until Fortune names the recipients.
10. Submit one automated smoke with `client_surface='benchmark'` and `automation_source='capture-verification'`; confirm it is persisted for aggregate auditing but absent from every evaluator account. Submit one browser-automation smoke on `replica` and confirm every evaluator sees the same **Automated** label.
