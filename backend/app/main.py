import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
import app.models  # noqa: F401  # 注册 ORM 元数据（含 admin_runtime_logs）
from app.routers import admin, auth, tasks
from app.services.admin_log_buffer import setup_admin_log_buffer
from app.services.admin_log_db import purge_expired_admin_runtime_logs
from app.services.daily_quota_reset import ensure_daily_reset_if_due


async def _admin_log_retention_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await purge_expired_admin_runtime_logs()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


async def _daily_quota_reset_loop() -> None:
    """RUN_WORKER_INLINE 等单进程部署时，不依赖 Celery Beat 也能按日重置 FREE 剩次数。"""
    while True:
        await asyncio.sleep(300)
        try:
            await ensure_daily_reset_if_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_admin_log_buffer()
    if settings.AUTO_CREATE_TABLES:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    purge_task: Optional[asyncio.Task] = None
    daily_reset_task: Optional[asyncio.Task] = None
    if int(settings.ADMIN_LOG_RETENTION_DAYS) > 0:
        try:
            await purge_expired_admin_runtime_logs()
        except Exception:
            pass
        purge_task = asyncio.create_task(_admin_log_retention_loop())
    try:
        await ensure_daily_reset_if_due()
    except Exception:
        pass
    daily_reset_task = asyncio.create_task(_daily_quota_reset_loop())
    yield
    for bg in (purge_task, daily_reset_task):
        if bg is not None:
            bg.cancel()
            try:
                await bg
            except asyncio.CancelledError:
                pass
    await engine.dispose()


app = FastAPI(title="PikPak Proxy Hub", lifespan=lifespan)

cors_origins = [s.strip() for s in settings.CORS_ALLOW_ORIGINS.split(",") if s.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(tasks.router)
api_router.include_router(tasks.download_router)
api_router.include_router(admin.router)

app.include_router(api_router)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}
