import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.auth import (
    ChangeAdminCredentialsRequest,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    TokenPair,
)
from app.schemas.user import UserCreate, UserPublic
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_session)) -> User:
    exists = await db.execute(select(User).where(User.username == body.username))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if body.email:
        e = await db.execute(select(User).where(User.email == body.email))
        if e.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="邮箱已注册")
    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        level="FREE",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_session)) -> LoginResponse:
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已禁用")
    uid = str(user.id)
    access = create_access_token(uid, user.level)
    refresh = create_refresh_token(uid)
    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        user=UserPublic.model_validate(user),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_session)) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="需要刷新令牌")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="无效刷新令牌")
    uid = uuid.UUID(str(sub))
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    u = str(user.id)
    return TokenPair(
        access_token=create_access_token(u, user.level),
        refresh_token=create_refresh_token(u),
    )


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(user)


@router.post("/admin/force-change", response_model=UserPublic)
async def admin_force_change(
    body: ChangeAdminCredentialsRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UserPublic:
    if user.level != "ADMIN":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if not getattr(user, "must_change_password", False):
        raise HTTPException(status_code=400, detail="无需强制修改密码")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="当前密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")

    if body.new_username and body.new_username != user.username:
        exists = await db.execute(select(User).where(User.username == body.new_username))
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="用户名已存在")
        user.username = body.new_username

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    await db.commit()
    await db.refresh(user)
    return UserPublic.model_validate(user)
