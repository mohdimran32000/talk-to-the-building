-- Module 7: Additional Tools (Text-to-SQL + Web Search)
-- Adds structured_data table for tabular data and tool settings

-- 1. Add tool settings to global_settings
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS text_to_sql_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS web_search_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS tavily_api_key TEXT;

UPDATE global_settings
SET text_to_sql_enabled = FALSE,
    web_search_enabled = FALSE
WHERE text_to_sql_enabled IS NULL;

-- 2. Create structured_data table for tabular data (CSV/XLSX)
CREATE TABLE IF NOT EXISTS structured_data (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id),
  table_name TEXT NOT NULL,
  columns JSONB NOT NULL,
  rows JSONB NOT NULL,
  row_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Index on document_id for cascade delete performance
CREATE INDEX IF NOT EXISTS structured_data_document_id_idx ON structured_data(document_id);
CREATE INDEX IF NOT EXISTS structured_data_user_id_idx ON structured_data(user_id);

-- 4. RLS: users read own rows, service_role full access
ALTER TABLE structured_data ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own structured_data"
  ON structured_data FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Service role full access on structured_data"
  ON structured_data FOR ALL
  USING (auth.role() = 'service_role');
