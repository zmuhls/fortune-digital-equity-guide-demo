-- Admit privacy-clear conversations created by the public Website Guide while
-- keeping automated benchmark and synthetic traffic out of the shared queue.

DROP TRIGGER IF EXISTS conversation_turns_ready_is_synthetic
    ON conversation_turns;
DROP FUNCTION IF EXISTS enforce_synthetic_review_ready();

CREATE OR REPLACE FUNCTION enforce_human_review_ready()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.review_state = 'ready' AND NOT EXISTS (
        SELECT 1
        FROM conversations AS c
        WHERE c.id = NEW.conversation_id
          AND c.capture_mode = 'transcript'
          AND c.client_surface IN ('replica', 'wix')
    ) THEN
        RAISE EXCEPTION 'review-ready turns must belong to a human Website Guide conversation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER conversation_turns_ready_is_human
    BEFORE INSERT OR UPDATE OF review_state, conversation_id
    ON conversation_turns
    FOR EACH ROW
    EXECUTE FUNCTION enforce_human_review_ready();

UPDATE conversation_turns AS t
SET review_state = 'pending'
FROM conversations AS c
WHERE c.id = t.conversation_id
  AND t.review_state = 'ready'
  AND (
      c.capture_mode <> 'transcript'
      OR c.client_surface NOT IN ('replica', 'wix')
  );

UPDATE conversation_turns AS t
SET review_state = 'ready'
FROM conversations AS c
WHERE c.id = t.conversation_id
  AND c.capture_mode = 'transcript'
  AND c.client_surface IN ('replica', 'wix')
  AND c.expires_at > NOW()
  AND t.status = 'complete'
  AND t.privacy_state = 'clear'
  AND (
      SELECT COUNT(*)
      FROM conversation_messages AS m
      WHERE m.turn_id = t.id
  ) = 2;

CREATE INDEX ix_conversations_human_review_recency
    ON conversations (last_turn_at DESC, id)
    WHERE capture_mode = 'transcript'
      AND client_surface IN ('replica', 'wix');
