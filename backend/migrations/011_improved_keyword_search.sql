-- Module 9: Improved Keyword Search for Technical Identifiers
-- Replaces plainto_tsquery with websearch_to_tsquery in the hybrid search RPC.
-- websearch_to_tsquery handles hyphenated codes (MDB-C-G3) better than
-- plainto_tsquery which breaks them into separate tokens (mdb & c & g3).

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
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(dc.tsv, websearch_to_tsquery('english', query_text)) DESC) AS keyword_rank
    FROM document_chunks dc
    JOIN documents d ON dc.document_id = d.id
    WHERE dc.user_id = match_user_id
      AND dc.tsv IS NOT NULL
      AND dc.tsv @@ websearch_to_tsquery('english', query_text)
      AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
    ORDER BY ts_rank_cd(dc.tsv, websearch_to_tsquery('english', query_text)) DESC
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
