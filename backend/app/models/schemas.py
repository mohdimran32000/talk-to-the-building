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
    tool_metadata: Optional[dict] = None
    created_at: datetime


class DocumentResponse(BaseModel):
    id: str
    user_id: Optional[str] = None       # CHANGED: nullable for scope='global' rows (Migration 012 coupling CHECK)
    file_name: str
    file_size: int
    mime_type: str
    status: str
    error_message: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[dict] = None
    content_markdown_status: Optional[str] = None  # 'ready' | 'pending' | 'failed' | 'requires_user_reupload' (Migration 014; D-03)
    folder_path: str = "/"              # NEW (Phase 3 / FOLDER-07) — default preserves existing-row response shape
    scope: str = "user"                 # NEW (Phase 3 / FOLDER-07) — default preserves existing-row response shape
    action: Optional[str] = None  # "created" | "skipped" | "updated" (only on upload response)
    created_at: datetime
    updated_at: datetime


class FolderResponse(BaseModel):
    id: str
    scope: str                          # 'user' | 'global'
    user_id: Optional[str] = None       # nullable for scope='global' rows
    path: str
    created_at: datetime


class FolderCreate(BaseModel):
    path: str
    scope: str = "user"                 # 'user' | 'global'


class FolderPatch(BaseModel):
    new_path: str


class RenameFolderResponse(FolderResponse):
    # FOLDER-03 contract: the rename endpoint returns the folder row PLUS atomic
    # counters from the rename_folder_prefix RPC (Migration 019). FolderResponse
    # alone would silently drop them via FastAPI's response_model serialization.
    documents_updated: int = 0
    folders_updated: int = 0


class FilePatch(BaseModel):
    # Mutable fields ONLY. scope is IMMUTABLE per Migration 015 forbid_scope_mutation trigger; Pydantic v2 ignores unknown fields by default, so a smuggled "scope" in the request body is silently dropped here (defense in depth alongside the DB trigger).
    file_name: Optional[str] = None
    folder_path: Optional[str] = None


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
    text_to_sql_enabled: bool = False
    web_search_enabled: bool = False
    tavily_api_key_set: bool = False
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
    text_to_sql_enabled: Optional[bool] = None
    web_search_enabled: Optional[bool] = None
    tavily_api_key: Optional[str] = None
