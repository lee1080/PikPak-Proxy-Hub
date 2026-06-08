from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from celery.schedules import crontab
from sqlalchemy import and_, select

from app.database import AsyncSessionLocal
from app.models.pikpak_account import PikPakAccount
from app.services.daily_quota_reset import reset_free_daily_limits
from app.models.task import Task
from app.services.encryption import decrypt_secret
from app.services.pikpak_driver import PikPakDriver
from app.workers.celery_app import celery_app

celery_app.conf.beat_schedule = {
    "cleanup-expired-files": {
        "task": "cleanup.cleanup_expired",
        "schedule": crontab(minute=0),
    },
    "reset-daily-task-limits": {
        "task": "cleanup.reset_daily_limits",
        "schedule": crontab(hour=0, minute=5),
    },
}


async def _cleanup_expired_async() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Task).where(
                # 仅清理由 Hub 明确标记为 EXPIRED 的任务文件（例如空间清理/管理员清盘）。
                # 不再按时间（expire_at）自动删除网盘文件。
                Task.status == "EXPIRED",
            ),
        )
        tasks = result.scalars().all()
        for task in tasks:
            if not task.file_id or not task.pikpak_account_id:
                continue
            acc = await db.get(PikPakAccount, task.pikpak_account_id)
            if not acc:
                continue
            pwd = decrypt_secret(acc.password_enc)
            driver = PikPakDriver(acc.email, pwd, acc.device_id, acc.refresh_token)
            try:
                await driver.ensure_token()
                await driver.delete_files([task.file_id])
                await driver.empty_trash()
            finally:
                acc.refresh_token = driver.refresh_token or acc.refresh_token
                await driver.aclose()
            task.status = "EXPIRED"
        await db.commit()


async def _reset_daily_async() -> None:
    async with AsyncSessionLocal() as db:
        await reset_free_daily_limits(db)


@celery_app.task(name="cleanup.cleanup_expired")
def cleanup_expired() -> None:
    asyncio.run(_cleanup_expired_async())


@celery_app.task(name="cleanup.reset_daily_limits")
def reset_daily_limits() -> None:
    asyncio.run(_reset_daily_async())
