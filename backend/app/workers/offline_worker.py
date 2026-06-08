from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.pikpak_account import PikPakAccount
from app.models.task import Task
from app.services.download_link_cache import replace_task_links
from app.services.cleanup import CleanupService
from app.services.encryption import decrypt_secret
from app.services.pikpak_driver import PikPakDriver
from app.services.space_policy import (
    account_free_bytes,
    looks_like_insufficient_space_http,
    premium_required_message,
    response_body_snippet,
    task_exceeds_pool_quota,
)
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


def _phase_done(phase: Optional[str]) -> bool:
    p = (phase or "").upper()
    return "COMPLETE" in p


def _phase_error(phase: Optional[str]) -> bool:
    p = (phase or "").upper()
    return "ERROR" in p or "FAIL" in p


def _sync_pool_refresh_token(account: PikPakAccount, driver: PikPakDriver) -> None:
    """PikPak 可能在 refresh / 401 重试中轮换 refresh_token，持久化避免其它请求拿到过期值。"""
    if driver.refresh_token:
        account.refresh_token = driver.refresh_token


async def _sync_account_quota(account: PikPakAccount, driver: PikPakDriver) -> None:
    quota = await driver.get_quota()
    account.quota_total = int(quota["limit"])
    account.quota_used = int(quota["usage"])
    account.last_synced_at = datetime.now(timezone.utc)


async def _fail_task_premium_required(task: Task, db) -> None:
    task.status = "FAILED"
    task.error_message = premium_required_message()
    await db.commit()


async def _add_offline_with_space_policy(
    *,
    task: Task,
    account: PikPakAccount,
    driver: PikPakDriver,
    cleanup: CleanupService,
    hub_id: str,
    task_uuid: uuid.UUID,
) -> dict:
    """
    1) 剩余空间足够：不清理，直接提交
    2) 不足：按最早完成时间 FIFO 清理旧任务后再提交
    3) 新任务体积超过号池总容量：直接 Premium 提示
    """
    required = max(0, int(task.file_size or 0))
    await _sync_account_quota(account, driver)

    if task_exceeds_pool_quota(required, int(account.quota_total or 0)):
        raise _PremiumRequired()

    free_now = account_free_bytes(int(account.quota_total), int(account.quota_used))
    if required > 0 and free_now < required:
        log.info(
            "offline_precheck_need_cleanup task_id=%s account_id=%s required=%s free=%s",
            task_uuid,
            account.id,
            required,
            free_now,
        )
        if not await cleanup.ensure_space(account, required, exclude_task_id=task.id):
            if task_exceeds_pool_quota(required, int(account.quota_total or 0)):
                raise _PremiumRequired()
            raise _CannotFreeEnoughSpace()

    try:
        return await driver.add_offline_task(task.source_url, folder_id=hub_id)
    except httpx.HTTPStatusError as exc:
        if not looks_like_insufficient_space_http(exc):
            raise

        log.warning(
            "offline_submit_insufficient_space_retry task_id=%s account_id=%s required=%s http=%s body=%s",
            task_uuid,
            account.id,
            required,
            exc.response.status_code,
            response_body_snippet(exc, 240),
        )
        await _sync_account_quota(account, driver)

        if task_exceeds_pool_quota(required, int(account.quota_total or 0)):
            raise _PremiumRequired()

        if required > 0:
            await cleanup.ensure_space(account, required, exclude_task_id=task.id)
        else:
            await cleanup.purge_all_completed_fifo(account, exclude_task_id=task.id)

        try:
            return await driver.add_offline_task(task.source_url, folder_id=hub_id)
        except httpx.HTTPStatusError as exc2:
            if not looks_like_insufficient_space_http(exc2):
                raise
            if required > 0:
                await cleanup.purge_all_completed_fifo(account, exclude_task_id=task.id)
                try:
                    return await driver.add_offline_task(task.source_url, folder_id=hub_id)
                except httpx.HTTPStatusError as exc3:
                    if looks_like_insufficient_space_http(exc3):
                        raise _PremiumRequired() from exc3
                    raise
            raise _PremiumRequired() from exc2


class _PremiumRequired(Exception):
    pass


class _CannotFreeEnoughSpace(Exception):
    pass


async def process_task_async(task_id: str) -> None:
    tid = uuid.UUID(task_id)
    account: Optional[PikPakAccount] = None
    async with AsyncSessionLocal() as db:
        task = await db.get(Task, tid)
        if task is None or not task.pikpak_account_id:
            return
        if task.status == "COMPLETED" and task.file_id:
            log.info(
                "offline_skip_already_done task_id=%s file_id=%s account_id=%s",
                tid,
                task.file_id,
                task.pikpak_account_id,
            )
            return

        log.info(
            "offline_start task_id=%s status=%s pool_account=%s link_type=%s content_key=%s source_prefix=%s",
            tid,
            task.status,
            task.pikpak_account_id,
            task.link_type,
            getattr(task, "content_key", None),
            (task.source_url or "")[:80],
        )

        account = await db.get(PikPakAccount, task.pikpak_account_id)
        if account is None:
            task.status = "FAILED"
            task.error_message = "关联 PikPak 账号不存在"
            await db.commit()
            return

        password = decrypt_secret(account.password_enc)
        driver = PikPakDriver(
            email=account.email,
            password=password,
            device_id=account.device_id,
            refresh_token=account.refresh_token,
        )
        try:
            await driver.ensure_token()
            account.refresh_token = driver.refresh_token
            hub_id = account.hub_folder_id
            if not hub_id:
                hub_id = await driver.ensure_pphub_folder_id()
                account.hub_folder_id = hub_id
                await db.commit()
            cleanup = CleanupService(db, driver)

            _sync_pool_refresh_token(account, driver)
            task.status = "SUBMITTED"
            await db.commit()

            try:
                resp = await _add_offline_with_space_policy(
                    task=task,
                    account=account,
                    driver=driver,
                    cleanup=cleanup,
                    hub_id=hub_id,
                    task_uuid=tid,
                )
            except _PremiumRequired:
                await _fail_task_premium_required(task, db)
                return
            except _CannotFreeEnoughSpace:
                task.status = "FAILED"
                task.error_message = "号池空间不足且无法腾出足够空间"
                await db.commit()
                return
            log.info(
                "offline_submitted_pikpak task_id=%s account_id=%s pool=%s",
                tid,
                account.id,
                account.pool_type,
            )
            data = resp if isinstance(resp, dict) else {}
            task_info = data.get("task") if isinstance(data.get("task"), dict) else {}
            file_info = data.get("file") if isinstance(data.get("file"), dict) else {}

            ptid = task_info.get("id") or data.get("task_id")
            fid_early = file_info.get("id")
            fname_early = file_info.get("name")
            size_early = file_info.get("size")

            task.pikpak_task_id = str(ptid) if ptid else None
            if fid_early:
                task.file_id = str(fid_early)
            if fname_early:
                task.file_name = str(fname_early)
            if size_early:
                task.file_size = int(size_early)
            _sync_pool_refresh_token(account, driver)
            await db.commit()

            if not ptid:
                task.status = "FAILED"
                task.error_message = "PikPak 未返回离线任务编号"
                await db.commit()
                return

            if account.pool_type == "FREE" and account.daily_tasks_left > 0:
                account.daily_tasks_left -= 1
                log.info(
                    "offline_free_pool_decrement task_id=%s account_id=%s daily_tasks_left=%s",
                    tid,
                    account.id,
                    account.daily_tasks_left,
                )
            await db.commit()

            poll_interval = max(4, settings.POLL_INTERVAL_SECONDS)
            max_iterations = int(timedelta(hours=18).total_seconds() // poll_interval)

            task.status = "DOWNLOADING"
            await db.commit()

            for _ in range(max_iterations):
                await asyncio.sleep(poll_interval)
                st = await driver.get_task_status(str(ptid))
                phase = st.get("phase")
                prog = int(st.get("progress") or 0)
                task.progress = max(int(task.progress or 0), prog)
                if st.get("file_id"):
                    task.file_id = str(st["file_id"])
                if st.get("file_name"):
                    task.file_name = str(st["file_name"])
                if st.get("file_size"):
                    try:
                        task.file_size = int(st["file_size"])
                    except (TypeError, ValueError):
                        pass
                msg = st.get("message")
                if msg:
                    task.error_message = str(msg)

                _sync_pool_refresh_token(account, driver)
                await db.commit()

                if _phase_done(phase):
                    task.status = "COMPLETED"
                    task.progress = 100
                    task.completed_at = datetime.now(timezone.utc)
                    # 任务与 PikPak 文件的绑定以 file_id 为准；只要文件未删除就不应因时间自动失效。
                    # expire_at 不再用于“文件过期清理”，仅保留为空（取回时按需取链）。
                    task.expire_at = None
                    quota2 = await driver.get_quota()
                    account.quota_used = quota2["usage"]
                    account.quota_total = quota2["limit"]
                    cached_url = ""
                    if task.file_id:
                        try:
                            cached_url = (await driver.get_download_url(str(task.file_id)) or "").strip()
                        except Exception:  # noqa: BLE001
                            cached_url = ""
                    if cached_url and task.file_id:
                        await replace_task_links(
                            db,
                            task.id,
                            [
                                {
                                    "file_id": str(task.file_id),
                                    "name": str(task.file_name or ""),
                                    "size": int(task.file_size or 0),
                                    "episode": {"season": None, "episode": None},
                                    "episode_key": None,
                                    "url": cached_url,
                                    "is_primary": True,
                                    "sort_index": 0,
                                }
                            ],
                        )
                    _sync_pool_refresh_token(account, driver)
                    await db.commit()
                    log.info(
                        "offline_completed task_id=%s file_id=%s account_id=%s file_name=%s expire_at=%s",
                        tid,
                        task.file_id,
                        task.pikpak_account_id,
                        (task.file_name or "")[:80],
                        task.expire_at.isoformat() if task.expire_at else None,
                    )
                    return

                if _phase_error(phase):
                    task.status = "FAILED"
                    await db.commit()
                    return

            task.status = "FAILED"
            task.error_message = "任务轮询超时"
            await db.commit()

        except httpx.HTTPStatusError as e:
            task.status = "FAILED"
            if looks_like_insufficient_space_http(e):
                required = max(0, int(task.file_size or 0))
                quota_total = int(account.quota_total or 0) if account else 0
                if task_exceeds_pool_quota(required, quota_total):
                    task.error_message = premium_required_message()
                else:
                    task.error_message = response_body_snippet(e)
            else:
                task.error_message = response_body_snippet(e)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            task.status = "FAILED"
            task.error_message = str(e)[:1000]
            await db.commit()
        finally:
            await driver.aclose()


@celery_app.task(name="offline.process_offline_task")
def process_offline_task(task_id: str) -> None:
    asyncio.run(process_task_async(task_id))
