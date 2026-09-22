-- Existing drafts stay drafts. Only a subsequent explicit Save & apply activates
-- team instructions. The existing append-only revision history is unchanged.
ALTER TABLE shared_prompt_drafts ADD COLUMN activated_version INTEGER;
ALTER TABLE shared_prompt_drafts ADD CONSTRAINT shared_prompt_activation_current
    CHECK (activated_version IS NULL OR activated_version = version);
