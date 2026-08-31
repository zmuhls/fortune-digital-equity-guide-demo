-- Record automation as explicit conversation provenance. Never infer it from
-- transcript wording, and never use it as a privacy classification.

ALTER TABLE conversations
    ADD COLUMN is_automated BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN automation_source TEXT;

UPDATE conversations
SET is_automated = TRUE,
    automation_source = client_surface
WHERE client_surface IN ('benchmark', 'synthetic');

ALTER TABLE conversations
    ADD CONSTRAINT conversations_automation_source_shape CHECK (
        automation_source IS NULL
        OR automation_source ~ '^[a-z0-9][a-z0-9._-]{0,79}$'
    ),
    ADD CONSTRAINT conversations_automation_source_requires_flag CHECK (
        is_automated OR automation_source IS NULL
    );

CREATE INDEX ix_conversations_automation_provenance
    ON conversations (is_automated, last_turn_at DESC);
