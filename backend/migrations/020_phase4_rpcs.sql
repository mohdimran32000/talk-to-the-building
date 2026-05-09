-- Phase 4 / Migration 020: Five-tools + search-extension RPCs.
-- Bundles (a) the new grep_documents RPC and (b) two CREATE OR REPLACE extensions
-- of match_document_chunks_with_filters and match_document_chunks_hybrid that gain
-- match_folder_path TEXT DEFAULT NULL + match_scope TEXT DEFAULT NULL (SEARCH-02).
-- Colocated here (vs. separate migration files) because they share the Phase 4
-- review surface; mirrors Phase 3's bundling of three folder RPCs into 019.
--
-- DESIGN NOTES:
-- 1. grep_documents wraps regex line-resolution + ILIKE GIN-trigram pre-filter +
--    pending_reindex surfacing in a single PL/pgSQL block. ILIKE narrows the
--    candidate set via documents_content_markdown_trgm_idx (Migration 016) BEFORE
--    the regex runs — without this, the regex falls back to seq-scan (Pitfall 3).
-- 2. SET LOCAL statement_timeout = '5s' inside the PL/pgSQL body bounds the regex
--    cost. PostgREST executes each .execute() in its own transaction; SET LOCAL
--    is scoped to that transaction (Pitfall 3 mitigation #5). Python wrapper has
--    no per-query GUC hook — DB-side is the only place this can live.
-- 3. CROSS JOIN LATERAL regexp_split_to_table(content_markdown, E'\n')
--    WITH ORDINALITY AS lines(line_text, line_no) produces 1-based line numbers
--    natively (perfect for arrow-form output in Plan 07's Python wrapper).
-- 4. pending_reindex surfacing — rows where content_markdown_status <> 'ready'
--    return a status='pending_reindex' row with NULL line_no/line_text. The 4-
--    element vocabulary 'pending'|'ready'|'failed'|'requires_user_reupload' is
--    Phase 1 / Migration 014 LOCKED. Tools NEVER silently skip non-ready docs.
-- 5. SECURITY INVOKER (the default; explicit for documentation) — RLS policies
--    from Migration 015 apply. Caller's `(SELECT auth.uid())` enforces user-scope
--    isolation; global-scope rows are readable by any authenticated user. The
--    p_scope arg is *narrowing* on top of RLS, never the access decision
--    (Pitfall 1 RANK 1 — research/ARCHITECTURE.md:362-363).
-- 6. match_document_chunks_with_filters and match_document_chunks_hybrid gain
--    two new TAIL-position parameters with DEFAULT NULL — backwards-compatible
--    with every existing call site (supabase-py / PostgREST does not require
--    keyword args to be sent). The new predicates apply identically to the
--    vector-results CTE AND the keyword-results CTE in the hybrid function.
-- 7. CREATE OR REPLACE FUNCTION is idempotent — re-running this migration is a
--    no-op (no errors). GRANT EXECUTE ... TO authenticated is also idempotent.
-- 8. NEVER use SECURITY DEFINER — that would bypass RLS and is a Pitfall 1 RANK 1
--    violation. The migration's own verifier asserts `SECURITY DEFINER` is absent.

-- ── 1. grep_documents (TOOL-03 — line-resolved regex with statement_timeout) ──
-- Returns one row per regex match (line-by-line via LATERAL regexp_split_to_table)
-- plus one row per pending_reindex doc within the path prefix.
-- ILIKE pre-filter on p_literal_substring exercises documents_content_markdown_trgm_idx
-- (Migration 016). Without an extractable literal substring (NULL p_literal_substring),
-- the GIN index can't help and the regex falls back to seq-scan over the path-prefix
-- bounded candidate set — still bounded; just slower. Caller (Plan 07) auto-extracts
-- a literal of length >= 3 from the regex when possible.

CREATE OR REPLACE FUNCTION public.grep_documents(
  p_pattern           TEXT,
  p_path_prefix       TEXT     DEFAULT '/',
  p_scope             TEXT     DEFAULT NULL,
  p_user_id           UUID     DEFAULT NULL,
  p_case_insensitive  BOOLEAN  DEFAULT TRUE,
  p_max_hits          INT      DEFAULT 50,
  p_literal_substring TEXT     DEFAULT NULL
)
RETURNS TABLE (
  document_id  UUID,
  file_name    TEXT,
  folder_path  TEXT,
  scope        TEXT,
  line_no      BIGINT,
  line_text    TEXT,
  status       TEXT
)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  -- Defense in depth: validate canonical path prefix (matches Migration 012 CHECK).
  IF p_path_prefix !~ '^/$|^/[^/]+(/[^/]+)*$' THEN
    RAISE EXCEPTION 'p_path_prefix not canonical: %', p_path_prefix
      USING ERRCODE = 'check_violation';
  END IF;

  -- Per-RPC statement timeout. SET LOCAL is scoped to the enclosing transaction
  -- (PostgREST opens one per .execute() call). Pitfall 3 mitigation #5.
  SET LOCAL statement_timeout = '5s';

  RETURN QUERY
  WITH candidates AS (
    SELECT d.id, d.file_name, d.folder_path, d.scope, d.content_markdown,
           d.content_markdown_status
    FROM public.documents d
    WHERE (d.folder_path = p_path_prefix
           OR d.folder_path LIKE p_path_prefix
              || (CASE WHEN p_path_prefix = '/' THEN '%' ELSE '/%' END))
      AND (p_scope IS NULL OR d.scope = p_scope)
      AND (p_literal_substring IS NULL
           OR (p_case_insensitive
               AND d.content_markdown ILIKE '%' || p_literal_substring || '%')
           OR (NOT p_case_insensitive
               AND d.content_markdown LIKE '%' || p_literal_substring || '%'))
  ),
  pending AS (
    -- Phase 2 LOCKED contract: surface non-ready docs as pending_reindex.
    SELECT c.id            AS document_id,
           c.file_name,
           c.folder_path,
           c.scope,
           NULL::BIGINT    AS line_no,
           NULL::TEXT      AS line_text,
           'pending_reindex'::TEXT AS status
    FROM candidates c
    WHERE c.content_markdown_status <> 'ready'
  ),
  matches AS (
    SELECT c.id            AS document_id,
           c.file_name,
           c.folder_path,
           c.scope,
           lines.line_no,
           lines.line_text,
           'matched'::TEXT AS status
    FROM candidates c
    CROSS JOIN LATERAL regexp_split_to_table(c.content_markdown, E'\n')
                       WITH ORDINALITY AS lines(line_text, line_no)
    WHERE c.content_markdown_status = 'ready'
      AND CASE
            WHEN p_case_insensitive THEN lines.line_text ~* p_pattern
            ELSE                          lines.line_text ~  p_pattern
          END
    LIMIT p_max_hits
  )
  SELECT * FROM matches
  UNION ALL
  SELECT * FROM pending
  LIMIT p_max_hits;
END;
$$;

GRANT EXECUTE ON FUNCTION public.grep_documents(TEXT, TEXT, TEXT, UUID, BOOLEAN, INT, TEXT) TO authenticated;

-- ── 2. match_document_chunks_with_filters (SEARCH-02 — gain match_folder_path + match_scope) ──
-- NEW PARAMS (tail-position, NULL defaults — backwards-compatible with every existing caller):
--   match_folder_path TEXT DEFAULT NULL  (NULL = no narrowing; non-NULL = anchored prefix predicate)
--   match_scope       TEXT DEFAULT NULL  (NULL = no narrowing; 'user' | 'global' = exact match)

CREATE OR REPLACE FUNCTION match_document_chunks_with_filters(
  query_embedding   vector(768),
  match_user_id     UUID,
  match_count       INT     DEFAULT 5,
  metadata_filter   JSONB   DEFAULT NULL,
  match_folder_path TEXT    DEFAULT NULL,
  match_scope       TEXT    DEFAULT NULL
)
RETURNS TABLE (id UUID, document_id UUID, content TEXT, similarity FLOAT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  RETURN QUERY
  SELECT dc.id, dc.document_id, dc.content,
         1 - (dc.embedding <=> query_embedding) AS similarity
  FROM document_chunks dc
  JOIN public.documents d ON dc.document_id = d.id
  WHERE dc.user_id = match_user_id
    AND dc.embedding IS NOT NULL
    AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
    AND (match_folder_path IS NULL
         OR d.folder_path = match_folder_path
         OR d.folder_path LIKE match_folder_path
            || (CASE WHEN match_folder_path = '/' THEN '%' ELSE '/%' END))
    AND (match_scope IS NULL OR d.scope = match_scope)
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION match_document_chunks_with_filters(vector(768), UUID, INT, JSONB, TEXT, TEXT) TO authenticated;

-- ── 3. match_document_chunks_hybrid (SEARCH-02 — gain match_folder_path + match_scope) ──
-- Same two new TAIL-position params as section 2; predicates applied IDENTICALLY in
-- BOTH the vector_results CTE AND the keyword_results CTE so RRF-merged results are
-- consistent under narrowing.

CREATE OR REPLACE FUNCTION match_document_chunks_hybrid(
  query_embedding    vector(768),
  query_text         TEXT,
  match_user_id      UUID,
  match_count        INT     DEFAULT 20,
  metadata_filter    JSONB   DEFAULT NULL,
  rrf_k              INT     DEFAULT 60,
  match_folder_path TEXT     DEFAULT NULL,
  match_scope       TEXT     DEFAULT NULL
)
RETURNS TABLE (id UUID, document_id UUID, content TEXT, rrf_score FLOAT)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  RETURN QUERY
  WITH vector_results AS (
    SELECT dc.id, dc.document_id, dc.content,
           ROW_NUMBER() OVER (ORDER BY dc.embedding <=> query_embedding) AS vector_rank
    FROM document_chunks dc
    JOIN public.documents d ON dc.document_id = d.id
    WHERE dc.user_id = match_user_id
      AND dc.embedding IS NOT NULL
      AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
      AND (match_folder_path IS NULL
           OR d.folder_path = match_folder_path
           OR d.folder_path LIKE match_folder_path
              || (CASE WHEN match_folder_path = '/' THEN '%' ELSE '/%' END))
      AND (match_scope IS NULL OR d.scope = match_scope)
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count * 2
  ),
  keyword_results AS (
    SELECT dc.id, dc.document_id, dc.content,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(dc.tsv, websearch_to_tsquery('english', query_text)) DESC) AS keyword_rank
    FROM document_chunks dc
    JOIN public.documents d ON dc.document_id = d.id
    WHERE dc.user_id = match_user_id
      AND dc.tsv IS NOT NULL
      AND dc.tsv @@ websearch_to_tsquery('english', query_text)
      AND (metadata_filter IS NULL OR d.metadata @> metadata_filter)
      AND (match_folder_path IS NULL
           OR d.folder_path = match_folder_path
           OR d.folder_path LIKE match_folder_path
              || (CASE WHEN match_folder_path = '/' THEN '%' ELSE '/%' END))
      AND (match_scope IS NULL OR d.scope = match_scope)
    ORDER BY ts_rank_cd(dc.tsv, websearch_to_tsquery('english', query_text)) DESC
    LIMIT match_count * 2
  ),
  combined AS (
    SELECT
      COALESCE(v.id, k.id) AS id,
      COALESCE(v.document_id, k.document_id) AS document_id,
      COALESCE(v.content, k.content) AS content,
      (COALESCE(1.0 / (rrf_k + v.vector_rank), 0)
       + COALESCE(1.0 / (rrf_k + k.keyword_rank), 0))::FLOAT AS rrf_score
    FROM vector_results v
    FULL OUTER JOIN keyword_results k ON v.id = k.id
  )
  SELECT combined.id, combined.document_id, combined.content, combined.rrf_score
  FROM combined
  ORDER BY combined.rrf_score DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION match_document_chunks_hybrid(vector(768), TEXT, UUID, INT, JSONB, INT, TEXT, TEXT) TO authenticated;
