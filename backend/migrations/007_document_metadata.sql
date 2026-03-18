-- Module 4: Metadata Extraction
-- Adds metadata column to documents, metadata_schema to global_settings, and filtered RPC

-- 1a. Add metadata column to documents
ALTER TABLE documents ADD COLUMN IF NOT EXISTS metadata JSONB;
CREATE INDEX IF NOT EXISTS documents_metadata_idx ON documents USING gin (metadata);

-- 1b. Add metadata_schema column to global_settings with defaults
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS metadata_schema JSONB
  DEFAULT '[
    {"name": "document_type", "type": "text", "required": true, "description": "Document category (e.g. report, email, article, manual, notes, code, other)"},
    {"name": "topic", "type": "text", "required": true, "description": "Primary topic in 2-5 words"},
    {"name": "summary", "type": "text", "required": true, "description": "1-3 sentence summary"},
    {"name": "language", "type": "text", "required": true, "description": "ISO 639-1 language code (e.g. en, es, fr)"},
    {"name": "entities", "type": "list", "required": false, "description": "Key people, organizations, dates, products (max 10)"},
    {"name": "keywords", "type": "list", "required": false, "description": "3-8 keywords for discoverability"},
    {"name": "is_technical", "type": "boolean", "required": false, "description": "Whether the document is technical in nature"},
    {"name": "page_count", "type": "number", "required": false, "description": "Number of pages or sections"},
    {"name": "publish_date", "type": "date", "required": false, "description": "Publication or creation date if mentioned (YYYY-MM-DD)"}
  ]'::jsonb;

-- Update existing row to have the default schema if null
UPDATE global_settings SET metadata_schema = '[
    {"name": "document_type", "type": "text", "required": true, "description": "Document category (e.g. report, email, article, manual, notes, code, other)"},
    {"name": "topic", "type": "text", "required": true, "description": "Primary topic in 2-5 words"},
    {"name": "summary", "type": "text", "required": true, "description": "1-3 sentence summary"},
    {"name": "language", "type": "text", "required": true, "description": "ISO 639-1 language code (e.g. en, es, fr)"},
    {"name": "entities", "type": "list", "required": false, "description": "Key people, organizations, dates, products (max 10)"},
    {"name": "keywords", "type": "list", "required": false, "description": "3-8 keywords for discoverability"},
    {"name": "is_technical", "type": "boolean", "required": false, "description": "Whether the document is technical in nature"},
    {"name": "page_count", "type": "number", "required": false, "description": "Number of pages or sections"},
    {"name": "publish_date", "type": "date", "required": false, "description": "Publication or creation date if mentioned (YYYY-MM-DD)"}
  ]'::jsonb WHERE metadata_schema IS NULL;

-- 1c. New RPC with metadata filtering
CREATE OR REPLACE FUNCTION match_document_chunks_with_filters(
  query_embedding  vector(768),
  match_user_id    UUID,
  match_count      INT DEFAULT 5,
  metadata_filter  JSONB DEFAULT NULL
)
RETURNS TABLE (id UUID, document_id UUID, content TEXT, similarity FLOAT)
LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  SELECT dc.id, dc.document_id, dc.content,
         1 - (dc.embedding <=> query_embedding) AS similarity
  FROM document_chunks dc
  JOIN documents d ON dc.document_id = d.id
  WHERE dc.user_id = match_user_id
    AND dc.embedding IS NOT NULL
    AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
