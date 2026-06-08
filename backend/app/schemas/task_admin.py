from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TaskAdminListItem(BaseModel):
    job_id: str
    user_email: Optional[str] = None
    account_email: Optional[str] = None
    pool_type: Optional[str] = None
    file_name: Optional[str] = None
    file_size: int = 0
    status: str
    progress: int = 0
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class TaskAdminListResponse(BaseModel):
    tasks: list[TaskAdminListItem]
    total: int
