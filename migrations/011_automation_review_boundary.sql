-- Keep nonparticipant automation outside the evaluator queue without deleting
-- transcripts. Public replica or Wix automation remains visible and labeled.

UPDATE conversation_turns AS t
SET review_state = 'excluded'
FROM conversations AS c
WHERE c.id = t.conversation_id
  AND c.client_surface IN ('benchmark', 'synthetic')
  AND t.review_state IN ('pending', 'ready');
