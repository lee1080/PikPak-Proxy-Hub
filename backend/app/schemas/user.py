from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    password: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    level: str
    must_change_password: bool = False


class UserAdminPatch(BaseModel):
    level: str


class UserAdminBatchDeleteRequest(BaseModel):
    user_ids: list[uuid.UUID]


class UserAdminResetPasswordRequest(BaseModel):
    new_password: str
