import os
import time
from app.auth import get_supabase_client

_cache = {"data": None, "expires": 0}
CACHE_TTL = 60  # seconds


_SETTINGS_COLUMNS = ",".join([
    "id", "llm_api_key", "llm_model", "langsmith_api_key", "langsmith_project",
    "langsmith_tracing", "metadata_schema", "hybrid_search_enabled",
    "reranking_enabled", "reranking_provider", "cohere_api_key",
    "text_to_sql_enabled", "web_search_enabled", "tavily_api_key",
    "updated_at", "updated_by",
])


def get_settings() -> dict:
    now = time.time()
    if _cache["data"] is not None and now < _cache["expires"]:
        return _cache["data"]

    sb = get_supabase_client()
    result = sb.table("global_settings").select(_SETTINGS_COLUMNS).eq("id", 1).single().execute()
    _cache["data"] = result.data
    _cache["expires"] = now + CACHE_TTL
    return result.data


def get_llm_api_key() -> str:
    settings = get_settings()
    key = settings.get("llm_api_key") if settings else None
    return key or os.environ.get("GEMINI_API_KEY", "")


def get_llm_model() -> str:
    settings = get_settings()
    model = settings.get("llm_model") if settings else None
    return model or "gemini-3-flash-preview"


DEFAULT_METADATA_SCHEMA = [
    {"name": "document_type", "type": "text", "required": True, "description": "Document category (e.g. report, email, article, manual, notes, code, other)"},
    {"name": "topic", "type": "text", "required": True, "description": "Primary topic in 2-5 words"},
    {"name": "summary", "type": "text", "required": True, "description": "1-3 sentence summary"},
    {"name": "language", "type": "text", "required": True, "description": "ISO 639-1 language code (e.g. en, es, fr)"},
    {"name": "entities", "type": "list", "required": False, "description": "Key people, organizations, dates, products (max 10)"},
    {"name": "keywords", "type": "list", "required": False, "description": "3-8 keywords for discoverability"},
    {"name": "is_technical", "type": "boolean", "required": False, "description": "Whether the document is technical in nature"},
    {"name": "page_count", "type": "number", "required": False, "description": "Number of pages or sections"},
    {"name": "publish_date", "type": "date", "required": False, "description": "Publication or creation date if mentioned (YYYY-MM-DD)"},
]


def get_metadata_schema() -> list[dict]:
    settings = get_settings()
    schema = settings.get("metadata_schema") if settings else None
    return schema or DEFAULT_METADATA_SCHEMA


def get_hybrid_search_enabled() -> bool:
    settings = get_settings()
    val = settings.get("hybrid_search_enabled") if settings else None
    return val if val is not None else True


def get_reranking_enabled() -> bool:
    settings = get_settings()
    val = settings.get("reranking_enabled") if settings else None
    return val if val is not None else False


def get_reranking_provider() -> str:
    settings = get_settings()
    val = settings.get("reranking_provider") if settings else None
    return val or "gemini"


def get_cohere_api_key() -> str:
    settings = get_settings()
    key = settings.get("cohere_api_key") if settings else None
    return key or ""


def get_text_to_sql_enabled() -> bool:
    settings = get_settings()
    val = settings.get("text_to_sql_enabled") if settings else None
    return val if val is not None else False


def get_web_search_enabled() -> bool:
    settings = get_settings()
    val = settings.get("web_search_enabled") if settings else None
    return val if val is not None else False


DEFAULT_SQL_LOOP_MAX_STEPS = 3
SQL_LOOP_MAX_STEPS_ENV = "SQL_LOOP_MAX_STEPS"


def get_sql_loop_max_steps() -> int:
    """How many SQL calls one question's investigation may make, from the
    `SQL_LOOP_MAX_STEPS` environment setting (default 3).

    An ENVIRONMENT setting, not a `global_settings` column, on purpose: it is the
    switch a BEFORE/AFTER measurement flips between two runs on the same commit,
    and it has to be flippable without a database write and without touching the
    settings any other question reads. `1` is the documented off position - spec
    4 binds it to "byte-identical to today".

    Anything unreadable falls back to the default, and so does anything below 1:
    a mistyped budget must make the loop behave normally, never silently switch
    it off, because a silently one-shot loop would look exactly like a loop that
    ran and found nothing. Read at call time so a deployment can change it.
    """
    raw = os.environ.get(SQL_LOOP_MAX_STEPS_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_SQL_LOOP_MAX_STEPS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_SQL_LOOP_MAX_STEPS
    return value if value >= 1 else DEFAULT_SQL_LOOP_MAX_STEPS


def get_tavily_api_key() -> str:
    settings = get_settings()
    key = settings.get("tavily_api_key") if settings else None
    return key or ""


def invalidate_cache():
    _cache["data"] = None
    _cache["expires"] = 0
