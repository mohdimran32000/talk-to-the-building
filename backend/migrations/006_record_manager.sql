-- Module 3: Record Manager — content hashing + unique constraint

-- Add content_hash column to documents
ALTER TABLE documents ADD COLUMN content_hash TEXT;

-- Add content_hash column to document_chunks (for chunk-level diffing)
ALTER TABLE document_chunks ADD COLUMN content_hash TEXT;

-- Unique constraint: one document per filename per user
-- This prevents duplicate filenames; re-uploads will UPDATE the existing row
ALTER TABLE documents ADD CONSTRAINT documents_user_filename_unique
  UNIQUE (user_id, file_name);

-- Index for fast hash lookups
CREATE INDEX documents_content_hash_idx ON documents(user_id, content_hash);
CREATE INDEX document_chunks_content_hash_idx ON document_chunks(document_id, content_hash);
