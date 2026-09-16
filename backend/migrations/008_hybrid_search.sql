-- Module 6: Hybrid Search & Reranking
-- Adds full-text search (tsvector), hybrid RRF search RPC, and reranking settings

-- 1. Add tsvector column to document_chunks
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv tsvector;

-- 2. Create GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS document_chunks_tsv_idx ON document_chunks USING gin(tsv);

-- 3. Auto-populate tsvector on INSERT/UPDATE via trigger
CREATE OR REPLACE FUNCTION document_chunks_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('english', NEW.content);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_chunks_tsv ON document_chunks;
CREATE TRIGGER trg_document_chunks_tsv
  BEFORE INSERT OR UPDATE OF content ON document_chunks
  FOR EACH ROW
  EXECUTE FUNCTION document_chunks_tsv_trigger();

-- 4. Backfill existing chunks
UPDATE document_chunks SET tsv = to_tsvector('english', content) WHERE tsv IS NULL;

-- 5. Hybrid search RPC: vector + keyword with RRF fusion
CREATE OR REPLACE FUNCTION match_document_chunks_hybrid(
  query_embedding  vector(768),
  query_text       TEXT,
  match_user_id    UUID,
  match_count      INT DEFAULT 20,
  metadata_filter  JSONB DEFAULT NULL,
  rrf_k            INT DEFAULT 60
)
RETURNS TABLE (id UUID, document_id UUID, content TEXT, rrf_score FLOAT)
LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  WITH vector_results AS (
    SELECT dc.id, dc.document_id, dc.content,
           ROW_NUMBER() OVER (ORDER BY dc.embedding <=> query_embedding) AS vector_rank
    FROM document_chunks dc
    JOIN documents d ON dc.document_id = d.id
    WHERE dc.user_id = match_user_id
      AND dc.embedding IS NOT NULL
      AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count * 2
  ),
  keyword_results AS (
    SELECT dc.id, dc.document_id, dc.content,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(dc.tsv, plainto_tsquery('english', query_text)) DESC) AS keyword_rank
    FROM document_chunks dc
    JOIN documents d ON dc.document_id = d.id
    WHERE dc.user_id = match_user_id
      AND dc.tsv IS NOT NULL
      AND dc.tsv @@ plainto_tsquery('english', query_text)
      AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
    ORDER BY ts_rank_cd(dc.tsv, plainto_tsquery('english', query_text)) DESC
    LIMIT match_count * 2
  ),
  combined AS (
    SELECT
      COALESCE(v.id, k.id) AS id,
      COALESCE(v.document_id, k.document_id) AS document_id,
      COALESCE(v.content, k.content) AS content,
      (COALESCE(1.0 / (rrf_k + v.vector_rank), 0) + COALESCE(1.0 / (rrf_k + k.keyword_rank), 0))::FLOAT AS rrf_score
    FROM vector_results v
    FULL OUTER JOIN keyword_results k ON v.id = k.id
  )
  SELECT combined.id, combined.document_id, combined.content, combined.rrf_score
  FROM combined
  ORDER BY combined.rrf_score DESC
  LIMIT match_count;
END;
$$;

-- 6. Add hybrid search settings to global_settings
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS hybrid_search_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS reranking_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS reranking_provider TEXT DEFAULT 'gemini';
ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS cohere_api_key TEXT;

UPDATE global_settings
SET hybrid_search_enabled = TRUE,
    reranking_enabled = FALSE,
    reranking_provider = 'gemini'
WHERE hybrid_search_enabled IS NULL;
