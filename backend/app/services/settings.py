import os
import time
from app.auth import get_supabase_client

_cache = {"data": None, "expires": 0}
CACHE_TTL = 60  # seconds


def get_settings() -> dict:
    now = time.time()
    if _cache["data"] is not None and now < _cache["expires"]:
        return _cache["data"]

    sb = get_supabase_client()
    result = sb.table("global_settings").select("*").eq("id", 1).single().execute()
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


def invalidate_cache():
    _cache["data"] = None
    _cache["expires"] = 0
