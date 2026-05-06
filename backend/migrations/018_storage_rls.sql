-- Phase 2 / Migration 018: Supabase Storage RLS for the 'documents' bucket.
-- Adds two policies on storage.objects gating per-user access to original
-- document blobs uploaded by ingest_document() (Phase 2 Storage Gap mitigation
-- per .planning/phases/02-content-markdown-backfill-gated/02-CONTEXT.md
-- §LOCKED—Storage Gap Resolution). Closes the foundational gap surfaced by
-- 02-RESEARCH.md §2: Episode 1 ingest discarded original bytes, leaving zero
-- recoverable source for any future re-Docling pass. Plan 03's
-- backfill_content_markdown.py downloads blobs via the inverse of the upload
-- path documents/{user_id}/{document_id}{ext} added in plan 01 / files.py.
--
-- ONE-TIME SETUP (NOT performed by this migration — operator action via
-- Supabase Studio): create the 'documents' bucket as PRIVATE before deploying
-- the Phase 2 application code. Studio path:
--   Storage -> Create bucket -> Name: documents, Public: OFF, File size limit: 50MB.
-- Equivalent SDK call (one-time, from a Python REPL using the service-role key):
--   supabase.storage.create_bucket("documents", options={"public": False})
-- Migration SQL is the wrong layer for bucket creation (bucket-level config
-- includes MIME allowlists and size limits that don't belong in DDL).
--
-- Path convention: documents/{user_id}/{document_id}{ext}
-- The user_id is at storage.foldername(name)[1] (Postgres array; 1-indexed),
-- enabling the auth.uid()::text = (storage.foldername(name))[1] RLS predicate
-- below. Mirrors the Phase 1 / Migration 015 convention of wrapping auth.uid()
-- as a perf-cached subquery (`(SELECT auth.uid())`) — see 01-PLAN.md plan 05.
--
-- Service-role automatically bypasses storage.objects RLS — both the FastAPI
-- server's get_supabase_client() (auth.py:8-12) and the backfill script
-- (Plan 03) use service-role and can read/write any blob without RLS friction.

-- SELECT policy: authenticated users can read only blobs in their own folder.
-- Idempotent via DROP POLICY IF EXISTS (matches migration 015 convention).
DROP POLICY IF EXISTS "documents_storage_select_own" ON storage.objects;
CREATE POLICY "documents_storage_select_own"
  ON storage.objects FOR SELECT
  TO authenticated
  USING (
    bucket_id = 'documents'
    AND (SELECT auth.uid())::text = (storage.foldername(name))[1]
  );

-- INSERT policy: authenticated users can write only into their own folder.
-- Defense in depth alongside the server-side path computation in
-- backend/app/routers/files.py::_upload_to_storage (path is built from the
-- JWT-validated user_id, so a malicious client cannot inject another user's
-- folder via filename — but RLS catches it even if app code regresses).
DROP POLICY IF EXISTS "documents_storage_insert_own" ON storage.objects;
CREATE POLICY "documents_storage_insert_own"
  ON storage.objects FOR INSERT
  TO authenticated
  WITH CHECK (
    bucket_id = 'documents'
    AND (SELECT auth.uid())::text = (storage.foldername(name))[1]
  );

-- No UPDATE / DELETE policies for the authenticated role this phase. Blobs are
-- write-once from the user's perspective (re-upload uses upsert=true which is
-- an INSERT with overwrite semantics). Service-role handles backend-side
-- delete-on-document-delete in a future phase.
