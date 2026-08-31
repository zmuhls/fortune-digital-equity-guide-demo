# Daily evaluation review

The production evaluation workspace is reviewed each day at 8:00 PM
America/New_York. The review covers the previous 24 hours and reports only what
changed:

- shared full-prompt draft edits, with display edit, change note, author, and
  time;
- module proposal revisions and comments;
- conversation-note and message-annotation revisions, with author and time;
- explicit evaluator-attribution changes;
- aggregate human and automated conversation counts, long-conversation counts,
  failures, model-call gaps, prompt provenance, response kinds, and latency.

The report may recommend concrete regression cases or implementation work and
asks no more than three concise questions requiring team judgment. It does not
apply prompt edits, recategorize transcripts, modify data, change code, or
deploy anything.

## Privacy boundary

The digest query never selects participant message text. Conversation
references are one-way shortened hashes rather than raw database identifiers.
Reviewer-authored notes, annotations, proposal comments, and prompt change
notes are included only after basic email and long-number redaction. The report
must not include credentials, evaluator account emails, invitation tokens,
cookies, raw database URLs, or private Railway variables.

## Operator check

Run the same content-safe review inside the production service with:

```bash
python3 scripts/daily_evaluation_digest.py --hours 24
```

An empty activity window is a valid result and should be reported briefly.
