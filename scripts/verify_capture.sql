\set ON_ERROR_STOP on
-- Pass -v prompt_policy=<version reported by the deployed /health endpoint>.

SELECT json_build_object(
    'schema_current', EXISTS (
        SELECT 1 FROM schema_migrations
        WHERE version = '009_human_review_capture'
    ),
    'evaluation_schema_current', EXISTS (
        SELECT 1 FROM schema_migrations
        WHERE version = '009_human_review_capture'
    ),
    'evaluation_slot_count', (
        SELECT COUNT(*) FROM evaluator_accounts
    ),
    'evaluation_unassigned_slot_count', (
        SELECT COUNT(*) FROM evaluator_accounts
        WHERE claimed_at IS NULL
          AND email_normalized IS NULL
          AND password_hash IS NULL
          AND invite_token_hash IS NULL
    ),
    'clear_turn_count', (
        SELECT COUNT(*) FROM conversation_turns
        WHERE id = :'clear_turn'::uuid
          AND client_event_id = :'clear_event'::uuid
          AND status = 'complete'
          AND review_state = 'pending'
          AND EXISTS (
              SELECT 1 FROM conversations c
              WHERE c.id = conversation_turns.conversation_id
                AND c.client_surface = 'benchmark'
          )
          AND chat_stage = 'opening'
          AND request_kind = 'clarification'
          AND request_language = 'en'
          AND response_language = 'en'
          AND prompt_policy_version = :'prompt_policy'
    ),
    'clear_message_count', (
        SELECT COUNT(*) FROM conversation_messages
        WHERE turn_id = :'clear_turn'::uuid
    ),
    'privacy_turn_count', (
        SELECT COUNT(*) FROM conversation_turns
        WHERE id = :'privacy_turn'::uuid
          AND client_event_id = :'privacy_event'::uuid
          AND status = 'complete'
          AND review_state = 'excluded'
          AND privacy_state = 'blocked'
    ),
    'privacy_message_count', (
        SELECT COUNT(*) FROM conversation_messages
        WHERE turn_id = :'privacy_turn'::uuid
    ),
    'sentinel_message_hits', (
        SELECT COUNT(*) FROM conversation_messages
        WHERE content LIKE '%' || :'sentinel' || '%'
    ),
    'sentinel_response_hits', (
        SELECT COUNT(*) FROM conversation_turns
        WHERE response_json::text LIKE '%' || :'sentinel' || '%'
    ),
    'continuation_token_hits', (
        SELECT COUNT(*) FROM conversation_turns
        WHERE response_json ? 'conversation_token'
    ),
    'server_owned_page_context', (
        SELECT page_context ->> 'source_id'
        FROM conversation_turns
        WHERE id = :'clear_turn'::uuid
    ),
    'expires_within_retention_window', (
        SELECT expires_at > NOW() AND expires_at <= NOW() + INTERVAL '91 days'
        FROM conversations
        WHERE id = :'clear_conversation'::uuid
    )
)::text;
