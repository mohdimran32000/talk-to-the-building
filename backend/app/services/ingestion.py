"""
Ingestion service for Module 2: BYO Retrieval.
Pipeline: update status → extract text → chunk → embed (batch) → insert chunks → update status
"""
import os
import io
import json
import logging
import time
from typing import List

from google import genai

from app.services.record_manager import compute_chunk_hash, compute_file_hash

logger = logging.getLogger(__name__)
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIMS = 768  # Truncate to 768 dims (pgvector ivfflat max is 2000)
_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


def extract_text(file_content: bytes, mime_type: str, file_name: str) -> str:
    ext = os.path.splitext(file_name)[1].lower()
    if mime_type == "application/pdf" or ext == ".pdf":
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_content))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ) or ext in (".docx", ".doc"):
        import docx as docx_lib
        doc = docx_lib.Document(io.BytesIO(file_content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if mime_type == "application/json" or ext == ".json":
        try:
            return json.dumps(json.loads(file_content.decode("utf-8", errors="replace")), indent=2)
        except Exception:
            pass
    return file_content.decode("utf-8", errors="replace")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks, start = [], 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end == len(words):
            break
        start = end - overlap
    return chunks


def _embed_with_retry(func, *args, max_retries: int = 3, **kwargs):
    """Call an embedding function with exponential backoff on failure."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(f"Embedding attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)


def embed_text(text: str) -> List[float]:
    response = _embed_with_retry(
        _client.models.embed_content,
        model=EMBEDDING_MODEL, contents=text,
        config={"output_dimensionality": EMBEDDING_DIMS},
    )
    return response.embeddings[0].values


def embed_batch(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    try:
        response = _embed_with_retry(
            _client.models.embed_content,
            model=EMBEDDING_MODEL, contents=texts,
            config={"output_dimensionality": EMBEDDING_DIMS},
        )
        return [e.values for e in response.embeddings]
    except Exception:
        return [embed_text(t) for t in texts]


def ingest_document(
    document_id: str,
    file_content: bytes,
    mime_type: str,
    file_name: str,
    user_id: str,
    supabase_client,
) -> None:
    try:
        supabase_client.table("documents").update(
            {"status": "processing", "updated_at": "now()"}
        ).eq("id", document_id).execute()

        text = extract_text(file_content, mime_type, file_name)
        if not text.strip():
            raise ValueError("No extractable text found in document")

        # Extract metadata before chunking
        try:
            from app.services.metadata import extract_metadata
            metadata = extract_metadata(text)
            supabase_client.table("documents").update(
                {"metadata": metadata, "updated_at": "now()"}
            ).eq("id", document_id).execute()
            logger.info(f"Extracted metadata for document {document_id}")
        except Exception as e:
            logger.warning(f"Metadata extraction failed (non-fatal) for {document_id}: {e}")

        chunks = chunk_text(text, chunk_size=500, overlap=50)
        if not chunks:
            raise ValueError("Chunking produced no chunks")

        embeddings = embed_batch(chunks)

        rows = [
            {
                "document_id": document_id,
                "user_id": user_id,
                "content": chunk,
                "embedding": embedding,
                "chunk_index": idx,
                "content_hash": compute_chunk_hash(chunk),
            }
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]
        for i in range(0, len(rows), 100):
            supabase_client.table("document_chunks").insert(rows[i : i + 100]).execute()

        file_hash = compute_file_hash(file_content)
        supabase_client.table("documents").update(
            {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
        ).eq("id", document_id).execute()
        logger.info(f"Ingested document {document_id}: {len(chunks)} chunks")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ingestion failed for document {document_id}: {error_msg}")
        try:
            supabase_client.table("documents").update(
                {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
            ).eq("id", document_id).execute()
        except Exception as inner_e:
            logger.error(f"Could not update failed status: {inner_e}")


def ingest_document_update(
    document_id: str,
    file_content: bytes,
    mime_type: str,
    file_name: str,
    user_id: str,
    supabase_client,
) -> None:
    """Re-ingest a document: delete all old chunks, re-chunk and re-embed from scratch."""
    try:
        supabase_client.table("documents").update(
            {"status": "processing", "updated_at": "now()"}
        ).eq("id", document_id).execute()

        # Delete all existing chunks
        supabase_client.table("document_chunks").delete().eq("document_id", document_id).execute()

        text = extract_text(file_content, mime_type, file_name)
        if not text.strip():
            raise ValueError("No extractable text found in document")

        # Re-extract metadata on update
        try:
            from app.services.metadata import extract_metadata
            metadata = extract_metadata(text)
            supabase_client.table("documents").update(
                {"metadata": metadata, "updated_at": "now()"}
            ).eq("id", document_id).execute()
            logger.info(f"Re-extracted metadata for document {document_id}")
        except Exception as e:
            logger.warning(f"Metadata extraction failed (non-fatal) for {document_id}: {e}")

        chunks = chunk_text(text, chunk_size=500, overlap=50)
        if not chunks:
            raise ValueError("Chunking produced no chunks")

        embeddings = embed_batch(chunks)

        rows = [
            {
                "document_id": document_id,
                "user_id": user_id,
                "content": chunk,
                "embedding": embedding,
                "chunk_index": idx,
                "content_hash": compute_chunk_hash(chunk),
            }
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]
        for i in range(0, len(rows), 100):
            supabase_client.table("document_chunks").insert(rows[i:i+100]).execute()

        file_hash = compute_file_hash(file_content)
        supabase_client.table("documents").update(
            {"status": "ready", "content_hash": file_hash, "updated_at": "now()"}
        ).eq("id", document_id).execute()

        logger.info(f"Re-ingested document {document_id}: {len(chunks)} chunks")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Update ingestion failed for {document_id}: {error_msg}")
        try:
            supabase_client.table("documents").update(
                {"status": "failed", "error_message": error_msg, "updated_at": "now()"}
            ).eq("id", document_id).execute()
        except Exception as inner_e:
            logger.error(f"Could not update failed status: {inner_e}")
