# Website Guide prompt history

This directory records the versioned default system prompt. Evaluators can edit
the complete shared System prompt in Prompts and apply it to subsequent messages.

- `manifest.json` is the release ledger. Historical entries are reconstructed
  from the named Git commit and say so explicitly.
- `versions/` contains human-readable snapshots of each meaningful prompt or
  prompt-behavior release.
- `current.md` contains the complete reviewed default system prompt.
- Runtime data such as the participant question, prior guide answer, current
  page ID, and approved candidate records is deliberately absent.

The default is assembled in `prompt_policy.py`. The active prompt is either that
default or the latest saved complete system prompt from PostgreSQL. It is shown
in Prompts using the same compilation function as the model request. Saved
revisions replace the prompt; no older prompt or additional instruction block is
appended. Source IDs are validated; natural model prose is not classified or
sent for a second generation. Historical version numbers
skip where a release changed routing or validation without creating a distinct
prompt artifact.

## Change process

1. Edit the complete System prompt in Prompts and describe the change concisely.
2. Save & apply commits a named, timestamped revision. The next message uses it;
   unsaved typing and older drafts are never activated implicitly.
3. A conflicting save preserves the evaluator's draft for reconciliation.
4. Transcript provenance records the deployed policy and active prompt revision.

Keep grounding, privacy, identity, and the response contract in the saved prompt
when editing it. Application source retrieval, data handling, and response
parsing remain separate server behavior. Module proposals and annotations remain
discussion material until incorporated into the System prompt.

Migration `015_single_system_prompt` corrects the earlier base-plus-draft
composition. It preserves the previous complete team prompt in append-only
history, then saves and activates a new revision based on the reviewed first
half of v1.36. The calendar audit adds instructions to follow actual labeled
signup links and select a supporting candidate when stating site facts. It does
not modify conversation or evaluation data.

Migration `016_source_linked_actions` appends and activates v1.38, preserving
all v1.37 and evaluator revisions. The model selects `pick` for the supporting
evidence and `action_url` for the next step it describes. The action destination
must be a URL supplied in the candidate records or their captured labeled links;
it does not have to be the page supplying the answer's facts. Ordinary replies
use `action_url: null`. Both fields are produced in the same model request.
