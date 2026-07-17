import logging
from typing import List

from google import genai
from google.genai import types
from pydantic import BaseModel
from langsmith import traceable

from app.services.settings import get_llm_api_key, get_llm_model, get_reranking_provider, get_cohere_api_key

logger = logging.getLogger(__name__)


class ChunkRelevance(BaseModel):
    index: int
    score: float


class RerankResult(BaseModel):
    rankings: list[ChunkRelevance]


# Relevance scoring is a cheap classification task — pin a fast non-thinking
# model instead of the (possibly thinking) chat model; measured 21.5s/rerank on
# gemini-3-flash-preview vs a few seconds on flash-lite.
RERANK_MODEL = "gemini-2.5-flash-lite"


def _rerank_gemini(query: str, chunks: List[str], top_k: int) -> List[str]:
    """Use Gemini to score each chunk's relevance to the query."""
    client = genai.Client(api_key=get_llm_api_key())
    model = RERANK_MODEL

    # Score on a bounded prefix — relevance is judged fine from the head of a
    # chunk, and full OCR-table chunks (median ~3.6k chars, max ~21k) make the
    # scoring call slow and expensive for no ranking gain.
    chunk_list = "\n\n".join(
        f"[Chunk {i}]: {chunk[:1200]}"
        for i, chunk in enumerate(chunks)
    )

    prompt = f"""Score each chunk's relevance to the query on a scale of 0.0 (irrelevant) to 1.0 (highly relevant).

Query: {query}

{chunk_list}

Return a JSON object with a "rankings" array, one entry per chunk. Each element should have "index" (chunk number) and "score" (0.0-1.0). Include every chunk — do not filter."""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RerankResult,
            temperature=0.0,
        ),
    )
    result = RerankResult.model_validate_json(response.text)
    sorted_rankings = sorted(result.rankings, key=lambda r: r.score, reverse=True)[:top_k]
    return [chunks[r.index] for r in sorted_rankings if r.index < len(chunks)]


def _rerank_cohere(query: str, chunks: List[str], top_k: int) -> List[str]:
    """Use Cohere Rerank API to reorder chunks by relevance."""
    import cohere

    api_key = get_cohere_api_key()
    if not api_key:
        logger.warning("Cohere API key not set, skipping reranking")
        return chunks[:top_k]

    client = cohere.ClientV2(api_key=api_key)
    response = client.rerank(
        model="rerank-v3.5",
        query=query,
        documents=chunks,
        top_n=top_k,
    )
    return [chunks[r.index] for r in response.results]


@traceable(name="rerank_chunks", run_type="chain")
def rerank_chunks(query: str, chunks: List[str], top_k: int = 5) -> List[str]:
    """Rerank chunks using the configured provider (Gemini or Cohere)."""
    if len(chunks) <= top_k:
        return chunks

    provider = get_reranking_provider()

    try:
        if provider == "cohere":
            return _rerank_cohere(query, chunks, top_k)
        else:
            return _rerank_gemini(query, chunks, top_k)
    except Exception as e:
        logger.warning(f"Reranking failed ({provider}), returning first {top_k} chunks: {e}")
        return chunks[:top_k]
