from fastapi import APIRouter, Depends
from app.auth import get_current_user, get_admin_user, get_user_profile, get_supabase_client
from app.models.schemas import GlobalSettingsResponse, GlobalSettingsUpdate, ProfileResponse
from app.services.settings import get_settings, get_llm_api_key, get_metadata_schema, invalidate_cache, get_hybrid_search_enabled, get_reranking_enabled, get_reranking_provider

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=GlobalSettingsResponse)
async def read_settings(user_id: str = Depends(get_current_user)):
    settings = get_settings()
    return GlobalSettingsResponse(
        llm_model=settings.get("llm_model"),
        langsmith_project=settings.get("langsmith_project"),
        langsmith_tracing=settings.get("langsmith_tracing", True),
        llm_api_key_set=bool(settings.get("llm_api_key")),
        langsmith_api_key_set=bool(settings.get("langsmith_api_key")),
        metadata_schema=get_metadata_schema(),
        hybrid_search_enabled=settings.get("hybrid_search_enabled", True),
        reranking_enabled=settings.get("reranking_enabled", False),
        reranking_provider=settings.get("reranking_provider", "gemini"),
        cohere_api_key_set=bool(settings.get("cohere_api_key")),
        updated_at=settings.get("updated_at"),
    )


@router.put("", response_model=GlobalSettingsResponse)
async def update_settings(
    data: GlobalSettingsUpdate,
    user_id: str = Depends(get_admin_user),
):
    update_data = data.model_dump(exclude_none=True)
    if not update_data:
        settings = get_settings()
    else:
        update_data["updated_by"] = user_id
        sb = get_supabase_client()
        result = sb.table("global_settings").update(update_data).eq("id", 1).execute()
        invalidate_cache()
        settings = get_settings()

    return GlobalSettingsResponse(
        llm_model=settings.get("llm_model"),
        langsmith_project=settings.get("langsmith_project"),
        langsmith_tracing=settings.get("langsmith_tracing", True),
        llm_api_key_set=bool(settings.get("llm_api_key")),
        langsmith_api_key_set=bool(settings.get("langsmith_api_key")),
        metadata_schema=get_metadata_schema(),
        hybrid_search_enabled=settings.get("hybrid_search_enabled", True),
        reranking_enabled=settings.get("reranking_enabled", False),
        reranking_provider=settings.get("reranking_provider", "gemini"),
        cohere_api_key_set=bool(settings.get("cohere_api_key")),
        updated_at=settings.get("updated_at"),
    )


@router.get("/models")
async def list_models(user_id: str = Depends(get_current_user)):
    """Fetch available Gemini models from the API, filtered to chat-capable ones."""
    from google import genai

    EXCLUDED_KEYWORDS = {"tts", "image", "embedding", "robotics", "computer-use", "deep-research", "banana"}

    try:
        client = genai.Client(api_key=get_llm_api_key())
        models = client.models.list()
        result = []
        for m in models:
            if "generateContent" not in (m.supported_actions or []):
                continue
            name = m.name.replace("models/", "")
            display = m.display_name or name
            if any(kw in name.lower() or kw in display.lower() for kw in EXCLUDED_KEYWORDS):
                continue
            result.append({"id": name, "name": display})
        return result
    except Exception as e:
        return [{"id": "gemini-3-flash-preview", "name": f"Gemini 3 Flash Preview (API error: {e})"}]


@router.get("/profile", response_model=ProfileResponse)
async def read_profile(user_id: str = Depends(get_current_user)):
    profile = get_user_profile(user_id)
    return ProfileResponse(**profile)
