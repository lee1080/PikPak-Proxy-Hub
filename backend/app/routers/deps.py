from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.user import User
from app.utils.security import decode_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(security),
    access_token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    token = cred.credentials if cred and cred.credentials else access_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = decode_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    if payload.get("type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请使用访问令牌")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
    uid = uuid.UUID(sub) if isinstance(sub, str) else sub
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.level != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    if getattr(user, "must_change_password", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="必须先修改管理员密码")
    return user


async def require_vip_or_above(user: User = Depends(get_current_user)) -> User:
    if user.level not in ("VIP", "ADMIN"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 VIP 权限")
    return user
