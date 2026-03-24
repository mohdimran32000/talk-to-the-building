from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class ThreadCreate(BaseModel):
    title: Optional[str] = None


class ThreadResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str
    metadata_filter: Optional[dict] = None


class MessageResponse(BaseModel):
    id: str
    thread_id: str
    role: str
    content: str
    created_at: datetime


class DocumentResponse(BaseModel):
    id: str
    user_id: str
    file_name: str
    file_size: int
    mime_type: str
    status: str
    error_message: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[dict] = None
    action: Optional[str] = None  # "created" | "skipped" | "updated" (only on upload response)
    created_at: datetime
    updated_at: datetime


class MetadataFieldDefinition(BaseModel):
    name: str
    type: str  # "text", "list", "boolean", "number", "date"
    required: bool = False
    description: str = ""


class ProfileResponse(BaseModel):
    id: str
    email: str
    is_admin: bool
    created_at: datetime
    updated_at: datetime


class GlobalSettingsResponse(BaseModel):
    llm_model: Optional[str] = None
    langsmith_project: Optional[str] = None
    langsmith_tracing: bool = True
    llm_api_key_set: bool = False
    langsmith_api_key_set: bool = False
    metadata_schema: Optional[list[dict]] = None
    hybrid_search_enabled: bool = True
    reranking_enabled: bool = False
    reranking_provider: str = "gemini"
    cohere_api_key_set: bool = False
    updated_at: Optional[datetime] = None


class GlobalSettingsUpdate(BaseModel):
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    langsmith_api_key: Optional[str] = None
    langsmith_project: Optional[str] = None
    langsmith_tracing: Optional[bool] = None
    metadata_schema: Optional[list[dict]] = None
    hybrid_search_enabled: Optional[bool] = None
    reranking_enabled: Optional[bool] = None
    reranking_provider: Optional[str] = None
    cohere_api_key: Optional[str] = None
