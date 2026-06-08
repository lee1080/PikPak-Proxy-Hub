"""运行日志落库（同步连接，供 logging Handler 线程安全写入）与异步按天清理。"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.admin_runtime_log import AdminRuntimeLog

_plog = logging.getLogger("app.admin_log_persist")
_plog.addHandler(logging.NullHandler())
_plog.propagate = False

_sync_engine = None
_SessionLocal: Optional[sessionmaker[Session]] = None
_persist_lock = threading.Lock()
_sync_url_failed = False


def _async_database_url_to_sync(url: str) -> str:
    u = url.strip()
    if "://" not in u:
        raise ValueError("invalid DATABASE_URL")
    scheme, rest = u.split("://", 1)
    mapping = {
        "sqlite+aiosqlite": "sqlite",
        "postgresql+asyncpg": "postgresql+psycopg",
    }
    if scheme not in mapping:
        raise ValueError(
            f"admin log DB persist: unsupported async scheme {scheme!r} "
            f"(supported: {', '.join(mapping)})"
        )
    return f"{mapping[scheme]}://{rest}"


def _get_sync_session_factory() -> sessionmaker[Session]:
    global _sync_engine, _SessionLocal
    if _SessionLocal is not None:
        return _SessionLocal

    sync_url = _async_database_url_to_sync(settings.DATABASE_URL)
    connect_args: dict = {}
    if sync_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _sync_engine = create_engine(
        sync_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args=connect_args,
    )
    _SessionLocal = sessionmaker(bind=_sync_engine, expire_on_commit=False, autoflush=False)
    return _SessionLocal


def try_persist_admin_runtime_log(
    *,
    created_at: datetime,
    level: str,
    logger_name: str,
    message: str,
) -> None:
    """在 logging Handler 中调用；失败静默（避免递归打日志）。"""
    global _sync_url_failed
    if int(settings.ADMIN_LOG_RETENTION_DAYS) <= 0:
        return
    if _sync_url_failed:
        return
    lv = (level or "")[:16]
    lg = (logger_name or "")[:256]
    msg = (message or "")[:8000]
    if not lv:
        lv = "INFO"
    try:
        fac = _get_sync_session_factory()
    except Exception as e:
        _sync_url_failed = True
        _plog.warning("admin log sync engine init failed: %s", e)
        return

    row = AdminRuntimeLog(
        id=uuid.uuid4(),
        created_at=created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc),
        level=lv,
        logger=lg,
        message=msg,
    )
    try:
        with _persist_lock:
            with fac() as session:
                session.add(row)
                session.commit()
    except Exception as e:
        _plog.warning("admin log persist failed: %s", e)


async def purge_expired_admin_runtime_logs() -> int:
    """删除早于保留策略的记录；返回删除行数（近似）。"""
    days = int(settings.ADMIN_LOG_RETENTION_DAYS)
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        res = await session.execute(delete(AdminRuntimeLog).where(AdminRuntimeLog.created_at < cutoff))
        await session.commit()
        return int(res.rowcount or 0)
