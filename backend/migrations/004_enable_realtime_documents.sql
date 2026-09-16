-- Enable Supabase Realtime on the documents table so the frontend
-- can listen for status changes via postgres_changes.
ALTER PUBLICATION supabase_realtime ADD TABLE documents;
