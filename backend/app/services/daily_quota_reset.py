from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.pikpak_account import PikPakAccount

logger = logging.getLogger(__name__)

# 与 pikpak_hub.db 同目录，记录上次已执行「每日重置」的日历日（按 DAILY_RESET_TIMEZONE）
_STATE_FILE = Path(__file__).resolve().parents[2] / ".daily_reset_date"


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.DAILY_RESET_TIMEZONE)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def today_reset_key() -> str:
    return datetime.now(_tz()).date().isoformat()


def last_reset_key() -> str:
    try:
        return _STATE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def mark_reset_done(today: str | None = None) -> None:
    _STATE_FILE.write_text(today or today_reset_key(), encoding="utf-8")


async def reset_free_daily_limits(db: AsyncSession) -> int:
    """将 FREE 池账号剩次数恢复为配置值，并重新启用（与 Celery cleanup 任务一致）。"""
    result = await db.execute(
        update(PikPakAccount)
        .where(PikPakAccount.pool_type == "FREE")
        .values(daily_tasks_left=settings.FREE_DAILY_TASK_LIMIT, status="ACTIVE"),
    )
    await db.commit()
    updated = int(result.rowcount or 0)
    mark_reset_done()
    logger.info(
        "daily_quota_reset FREE pool reset: updated=%s limit=%s date=%s",
        updated,
        settings.FREE_DAILY_TASK_LIMIT,
        today_reset_key(),
    )
    return updated


async def ensure_daily_reset_if_due() -> dict:
    """若当前时区已进入新一天且尚未重置，则执行 FREE 池每日次数恢复。"""
    today = today_reset_key()
    if last_reset_key() == today:
        return {"skipped": True, "date": today, "updated": 0}
    async with AsyncSessionLocal() as db:
        updated = await reset_free_daily_limits(db)
    return {"skipped": False, "date": today, "updated": updated, "limit": settings.FREE_DAILY_TASK_LIMIT}
