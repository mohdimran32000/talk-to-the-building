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
    created_at: datetime
    updated_at: datetime


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
    updated_at: Optional[datetime] = None


class GlobalSettingsUpdate(BaseModel):
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    langsmith_api_key: Optional[str] = None
    langsmith_project: Optional[str] = None
    langsmith_tracing: Optional[bool] = None
