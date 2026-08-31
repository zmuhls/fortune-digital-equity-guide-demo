-- Collaborative prompt drafting, durable evaluator feedback history, and
-- explicit conversation attribution. Participant transcript text is never
-- copied into any of these review-history tables.

CREATE TABLE shared_prompt_drafts (
    scope_key TEXT PRIMARY KEY
        REFERENCES prompt_review_workspaces(scope_key) ON DELETE CASCADE,
    release_number INTEGER NOT NULL CHECK (release_number > 0),
    edit_number INTEGER NOT NULL CHECK (edit_number > 0),
    body TEXT NOT NULL CHECK (LENGTH(body) BETWEEN 1 AND 20000),
    change_note TEXT NOT NULL CHECK (LENGTH(change_note) BETWEEN 1 AND 500),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by TEXT NOT NULL REFERENCES evaluator_accounts(slot_key),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE shared_prompt_draft_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope_key TEXT NOT NULL
        REFERENCES prompt_review_workspaces(scope_key) ON DELETE CASCADE,
    operation_id UUID UNIQUE,
    release_number INTEGER NOT NULL CHECK (release_number > 0),
    edit_number INTEGER NOT NULL CHECK (edit_number > 0),
    body TEXT NOT NULL CHECK (LENGTH(body) BETWEEN 1 AND 20000),
    change_note TEXT NOT NULL CHECK (LENGTH(change_note) BETWEEN 1 AND 500),
    actor_slot TEXT NOT NULL REFERENCES evaluator_accounts(slot_key),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scope_key, release_number, edit_number)
);

CREATE TABLE conversation_note_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id UUID UNIQUE,
    bucket_set_id UUID NOT NULL
        REFERENCES evaluation_bucket_sets(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    evaluation_version INTEGER NOT NULL CHECK (evaluation_version > 0),
    note TEXT CHECK (note IS NULL OR LENGTH(note) <= 1000),
    action TEXT NOT NULL CHECK (action IN ('save', 'remove')),
    actor_slot TEXT NOT NULL REFERENCES evaluator_accounts(slot_key),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_conversation_note_revisions_recent
    ON conversation_note_revisions (recorded_at DESC);

CREATE TABLE conversation_annotation_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id UUID UNIQUE,
    bucket_set_id UUID NOT NULL
        REFERENCES evaluation_bucket_sets(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    message_id UUID NOT NULL,
    annotation_version INTEGER NOT NULL CHECK (annotation_version > 0),
    category TEXT CHECK (
        category IS NULL OR category IN ('helpful', 'unclear', 'incorrect', 'unsafe', 'other')
    ),
    note TEXT CHECK (note IS NULL OR LENGTH(note) <= 500),
    action TEXT NOT NULL CHECK (action IN ('save', 'remove')),
    actor_slot TEXT NOT NULL REFERENCES evaluator_accounts(slot_key),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_conversation_annotation_revisions_recent
    ON conversation_annotation_revisions (recorded_at DESC);

ALTER TABLE conversations
    ADD COLUMN evaluator_slot TEXT REFERENCES evaluator_accounts(slot_key) ON DELETE SET NULL,
    ADD COLUMN evaluator_attribution_version INTEGER NOT NULL DEFAULT 0
        CHECK (evaluator_attribution_version >= 0),
    ADD COLUMN evaluator_attribution_source TEXT CHECK (
        evaluator_attribution_source IS NULL
        OR evaluator_attribution_source IN ('session', 'manual', 'recovered')
    ),
    ADD COLUMN evaluator_attributed_by TEXT
        REFERENCES evaluator_accounts(slot_key) ON DELETE SET NULL,
    ADD COLUMN evaluator_attributed_at TIMESTAMPTZ;

CREATE TABLE conversation_attribution_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id UUID UNIQUE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    attribution_version INTEGER NOT NULL CHECK (attribution_version > 0),
    evaluator_slot TEXT REFERENCES evaluator_accounts(slot_key) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK (source IN ('session', 'manual', 'recovered')),
    actor_slot TEXT NOT NULL REFERENCES evaluator_accounts(slot_key),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (conversation_id, attribution_version)
);

CREATE INDEX ix_conversation_attribution_revisions_recent
    ON conversation_attribution_revisions (recorded_at DESC);

CREATE FUNCTION record_conversation_attribution_change()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT' AND NEW.evaluator_slot IS NOT NULL)
       OR (TG_OP = 'UPDATE' AND NEW.evaluator_slot IS DISTINCT FROM OLD.evaluator_slot) THEN
        INSERT INTO conversation_attribution_revisions (
            conversation_id, attribution_version, evaluator_slot,
            source, actor_slot, recorded_at
        ) VALUES (
            NEW.id, NEW.evaluator_attribution_version, NEW.evaluator_slot,
            COALESCE(NEW.evaluator_attribution_source, 'session'),
            COALESCE(NEW.evaluator_attributed_by, NEW.evaluator_slot),
            COALESCE(NEW.evaluator_attributed_at, NOW())
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER conversations_record_evaluator_attribution
AFTER INSERT OR UPDATE OF evaluator_slot ON conversations
FOR EACH ROW EXECUTE FUNCTION record_conversation_attribution_change();

-- Preserve the current shared notes and annotations as the first recoverable
-- revision before future saves begin appending exact history.
INSERT INTO conversation_note_revisions (
    bucket_set_id, conversation_id, evaluation_version, note,
    action, actor_slot, recorded_at
)
SELECT bucket_set_id, conversation_id, version, note,
       CASE WHEN note IS NULL THEN 'remove' ELSE 'save' END,
       updated_by, updated_at
FROM conversation_evaluations
WHERE note IS NOT NULL;

INSERT INTO conversation_annotation_revisions (
    bucket_set_id, conversation_id, message_id, annotation_version,
    category, note, action, actor_slot, recorded_at
)
SELECT bucket_set_id, conversation_id, message_id, version,
       category, note, 'save', updated_by, updated_at
FROM conversation_annotations;

CREATE FUNCTION prevent_shared_review_history_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'shared review history is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER shared_prompt_draft_revisions_append_only
BEFORE UPDATE OR DELETE ON shared_prompt_draft_revisions
FOR EACH ROW EXECUTE FUNCTION prevent_shared_review_history_mutation();

CREATE TRIGGER conversation_note_revisions_append_only
BEFORE UPDATE OR DELETE ON conversation_note_revisions
FOR EACH ROW EXECUTE FUNCTION prevent_shared_review_history_mutation();

CREATE TRIGGER conversation_annotation_revisions_append_only
BEFORE UPDATE OR DELETE ON conversation_annotation_revisions
FOR EACH ROW EXECUTE FUNCTION prevent_shared_review_history_mutation();

CREATE TRIGGER conversation_attribution_revisions_append_only
BEFORE UPDATE OR DELETE ON conversation_attribution_revisions
FOR EACH ROW EXECUTE FUNCTION prevent_shared_review_history_mutation();

ALTER TABLE evaluation_audit_events
    DROP CONSTRAINT evaluation_audit_events_action_check,
    ADD CONSTRAINT evaluation_audit_events_action_check CHECK (action IN (
        'bucket.create', 'bucket.update', 'bucket.archive',
        'conversation.move', 'conversation.note', 'conversation.annotation',
        'conversation.attribute',
        'account.invite', 'account.claim', 'account.disable'
    ));
