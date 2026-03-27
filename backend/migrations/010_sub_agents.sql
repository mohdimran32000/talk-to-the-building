-- Module 8: Sub-Agents
-- Add tool_metadata column to store structured tool/sub-agent info on messages

ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_metadata JSONB;

-- Existing RLS policies on messages already cover this column (row-level, not column-level).
-- Old messages get NULL by default.
