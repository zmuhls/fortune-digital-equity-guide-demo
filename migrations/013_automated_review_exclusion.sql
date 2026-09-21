-- Keep every explicitly automated Website Guide run out of the shared human
-- evaluator queue. Preserve the conversation and transcript for bounded
-- operational auditing; only change its review eligibility.

UPDATE conversation_turns AS t
SET review_state = 'excluded'
FROM conversations AS c
WHERE c.id = t.conversation_id
  AND c.is_automated
  AND t.review_state IN ('pending', 'ready');
