from typing import Optional

from pydantic import BaseModel

from app.schemas.user import UserPublic


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(TokenPair):
    user: UserPublic


class ChangeAdminCredentialsRequest(BaseModel):
    # 管理员首次登录强制改密：账号可选修改
    current_password: str
    new_password: str
    new_username: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str
