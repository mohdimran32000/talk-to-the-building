-- Migration 021: Rebuild the dead vector index on document_chunks.embedding.
--
-- ROOT CAUSE (found during the doc-QA audit, 2026-07-17): Migration 003 created
-- an ivfflat index (lists = 100) on document_chunks WHILE THE TABLE WAS EMPTY.
-- An ivfflat index built on an empty table has degenerate list centroids, and
-- with the default ivfflat.probes = 1 every ANN scan returns ZERO rows — the
-- planner uses the index for `ORDER BY embedding <=> $1 LIMIT n`, so BOTH
-- match_document_chunks_with_filters AND the vector leg of
-- match_document_chunks_hybrid have silently returned 0 rows for every user
-- since Phase 1. Hybrid search masked it: the keyword (tsv) leg still returned
-- results, so retrieval "worked" — as keyword-only search.
--
-- FIX: replace ivfflat with HNSW. HNSW builds incrementally and has no
-- "trained on empty table" failure mode — correct results regardless of how
-- much data existed at index-creation time. pgvector >= 0.5 on Supabase
-- supports it natively.
--
-- Idempotent: DROP IF EXISTS + CREATE IF NOT EXISTS.

DROP INDEX IF EXISTS document_chunks_embedding_idx;

CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
  ON document_chunks USING hnsw (embedding vector_cosine_ops);
