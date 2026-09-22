# Website Guide prompt history

This directory records the versioned base policy. Evaluators can edit shared
team instructions in Prompts and apply them to subsequent messages.

- `manifest.json` is the release ledger. Historical entries are reconstructed
  from the named Git commit and say so explicitly.
- `versions/` contains human-readable snapshots of each meaningful prompt or
  prompt-behavior release.
- `current.md` describes the compiled policy and the boundary between fixed
  server invariants and team-tunable presentation choices.
- Runtime data such as the participant question, prior guide answer, current
  page ID, and approved candidate records is deliberately absent.

The base policy is `prompt_policy.py`. The current compiled prompt combines it
with the latest explicitly saved team instructions from PostgreSQL and is shown
in Prompts. Source IDs are validated; natural model prose is not classified or
sent for a second generation. Historical version numbers
skip where a release changed routing or validation without creating a distinct
prompt artifact.

## Change process

1. Edit Team instructions in Prompts and describe the change concisely.
2. Save & apply commits a named, timestamped revision. The next message uses it;
   unsaved typing and older drafts are never activated implicitly.
3. A conflicting save preserves the evaluator's draft for reconciliation.
4. Transcript provenance records the base policy and applied team revision.

Source grounding, privacy, identity, and rephrasing limits remain base-policy
boundaries. Changes to that base still require tests and deployment. Module
proposals and annotations remain discussion material until incorporated into
Team instructions; they do not silently become model instructions.
