from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    url: str = Field(min_length=3)


class TaskSubmitResponse(BaseModel):
    job_id: uuid.UUID
    link_type: str
    status: str
    message: str


class TaskListItem(BaseModel):
    job_id: str
    file_name: Optional[str]
    file_size: int
    status: str
    progress: int
    download_url: Optional[str]
    created_at: Optional[str]
    error_message: Optional[str] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskListItem]
    total: int


class TaskDeleteResponse(BaseModel):
    ok: bool = True
    deleted: int


class TaskDownloadLinkResponse(BaseModel):
    """url 为 Hub 签名路径；provider_url 在 resolve_provider=true 时为 PikPak 真实下载链（可直接打开）。"""
    url: str
    expires_in: int
    provider_url: Optional[str] = None


class TaskPlayUrlResponse(BaseModel):
    """合并取链：优先 DB/内存缓存，miss 时单次回源 PikPak。"""
    url: str
    file_id: str
    from_cache: bool = False
    expires_at: Optional[int] = None


class TaskDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    source_url: str
    link_type: str
    file_name: Optional[str]
    file_size: int
    status: str
    progress: int
    pool_type: Optional[str]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]
