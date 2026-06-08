from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.responses import PlainTextResponse
from sqlalchemy import case, delete, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, get_session
from app.models.pikpak_account import PikPakAccount
from app.models.task import Task
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.task import (
    TaskCreate,
    TaskDeleteResponse,
    TaskDownloadLinkResponse,
    TaskDetail,
    TaskListItem,
    TaskListResponse,
    TaskPlayUrlResponse,
    TaskSubmitResponse,
)
from app.services.encryption import decrypt_secret
from app.services.content_key import content_key_for_url
from app.services.download_link_cache import clear_task_links, get_valid_task_links, replace_task_links
from app.services.file_link_memory_cache import get_cached_file_link, set_cached_file_link
from app.services.pikpak_url_utils import pikpak_url_expire_epoch, pikpak_url_is_expired
from app.services.link_parser import (
    LinkType,
    detect_link_type,
    estimate_file_size,
    parse_torrent_file,
)
from app.services.pikpak_driver import PikPakDriver
from app.services.scheduler import pick_account_for_user, pool_type_for_user_level
from app.utils.security import create_download_token, decode_token

router = APIRouter(prefix="/tasks", tags=["tasks"])
download_router = APIRouter(tags=["download"])
USED_DOWNLOAD_JTIS: dict[str, datetime] = {}

log = logging.getLogger(__name__)

# 用户可见文案保持简短，具体原因只打日志供管理员排查
_USER_DOWNLOAD_RETRY_MSG = "取回处理时间较长，请稍后在任务列表中查看进度"

# 号池 worker 与其它请求并发刷新时，DB 里可能已是新 refresh_token，内存仍为旧值
_PIKPAK_TOKEN_AUTH_RETRIES = 3


def _normalized_video_exts() -> set[str]:
    return {s.strip().lower().lstrip(".") for s in (settings.DOWNLOAD_FOLDER_VIDEO_EXTS or "").split(",") if s.strip()}


def _normalized_hide_keywords() -> list[str]:
    return [s.strip().lower() for s in (settings.DOWNLOAD_FOLDER_HIDE_KEYWORDS or "").split(",") if s.strip()]


def _looks_like_video_file(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if "." not in n:
        return False
    ext = n.rsplit(".", 1)[-1].lower()
    return ext in _normalized_video_exts()


def _matches_hide_keywords(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in _normalized_hide_keywords())


_EP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bS(?P<s>\d{1,2})\s*E(?P<e>\d{1,3})\b"),
    re.compile(r"(?i)\bEP?\s*(?P<e>\d{1,3})\b"),
    re.compile(r"第\s*(?P<e>\d{1,3})\s*集"),
]


def _parse_episode(name: str) -> dict[str, Optional[int]]:
    s: Optional[int] = None
    e: Optional[int] = None
    for pat in _EP_PATTERNS:
        m = pat.search(name or "")
        if not m:
            continue
        if "s" in m.groupdict():
            try:
                s = int(m.group("s"))
            except Exception:  # noqa: BLE001
                s = None
        if "e" in m.groupdict():
            try:
                e = int(m.group("e"))
            except Exception:  # noqa: BLE001
                e = None
        if e is not None or s is not None:
            break
    return {"season": s, "episode": e}


def _episode_key_str(season: Optional[int], episode: Optional[int]) -> Optional[str]:
    if episode is None:
        return None
    if season is None:
        return f"E{episode:02d}"
    return f"S{season:02d}E{episode:02d}"


_TAIL_NUM_RE = re.compile(r"(?i)(?P<n>\d{1,4})\s*(?:\.[a-z0-9]{1,5})?$")


def _extract_trailing_number(name: str) -> Optional[int]:
    """
    兜底：文件名末尾数字（常见于 xxx.07.mp4 / xxx_007.mkv）。
    若末尾数字过大（像 2160/1080 等分辨率）也可能误判，但仅在无集数标记时才使用。
    """
    n = (name or "").strip()
    if not n:
        return None
    m = _TAIL_NUM_RE.search(n)
    if not m:
        return None
    try:
        val = int(m.group("n"))
    except Exception:  # noqa: BLE001
        return None
    # 过大的末尾数字更可能是分辨率/年份；简单阈值避免离谱排序
    if val > 500:
        return None
    return val


def _episode_sort_key(name: str) -> tuple[int, int, int, int, str]:
    """
    强一致排序规则：
    0) SxxExx
    1) Exx / 第xx集
    2) 末尾数字
    3) A-Z
    """
    ep = _parse_episode(name)
    s = ep.get("season")
    e = ep.get("episode")
    if isinstance(s, int) and isinstance(e, int):
        return (0, s, e, 0, (name or "").lower())
    if e is not None:
        return (1, 0, int(e), 0, (name or "").lower())
    tail = _extract_trailing_number(name)
    if tail is not None:
        return (2, 0, int(tail), 0, (name or "").lower())
    return (3, 0, 0, 0, (name or "").lower())


def _smart_min_size_bytes(sizes: list[int]) -> int:
    """
    sizes: 已经是“疑似视频+未命中关键字”的候选体积列表（字节）。
    目标：在不同体量目录下也能过滤广告/短视频。
    """
    if not sizes:
        return int(settings.DOWNLOAD_FOLDER_MIN_SIZE_BYTES)
    sizes2 = sorted([int(x) for x in sizes if int(x) > 0])
    if not sizes2:
        return int(settings.DOWNLOAD_FOLDER_MIN_SIZE_BYTES)
    median = sizes2[len(sizes2) // 2]
    floor_b = int(settings.DOWNLOAD_FOLDER_MIN_SIZE_FLOOR_BYTES)
    cap_b = int(settings.DOWNLOAD_FOLDER_MIN_SIZE_BYTES)
    # 经验：取 median 的 60% 作为阈值，再夹在 [floor, cap] 内
    t = int(median * 0.6)
    return max(floor_b, min(cap_b, t))


def _http_error_is_pikpak_invalid_grant(exc: httpx.HTTPStatusError) -> bool:
    """PikPak 使用 OAuth 风格错误体；4126 表示 refresh 已被其它会话占用。"""
    if exc.response.status_code != 400:
        return False
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            if body.get("error") == "invalid_grant":
                return True
            code = body.get("error_code")
            if code == 4126 or str(code) == "4126":
                return True
    except Exception:  # noqa: BLE001 — 仅用于判断是否可重试
        pass
    return "invalid_grant" in (exc.response.text or "").lower()


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _consume_once_jti(jti: str, exp_ts: int) -> None:
    now = datetime.now(timezone.utc)
    expired = [k for k, v in USED_DOWNLOAD_JTIS.items() if v <= now]
    for k in expired:
        USED_DOWNLOAD_JTIS.pop(k, None)
    if jti in USED_DOWNLOAD_JTIS:
        raise HTTPException(status_code=410, detail="签名链接已使用或已失效")
    expire_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
    USED_DOWNLOAD_JTIS[jti] = expire_at


async def _invalidate_tasks_for_stale_file(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    file_id: str,
    error_message: str,
) -> int:
    """同一网盘 file_id 可能被多条任务复用；取链失败时一并失效，避免幽灵缓存。返回影响行数。"""
    task_ids_result = await db.execute(
        select(Task.id)
        .where(Task.pikpak_account_id == account_id)
        .where(Task.file_id == file_id)
    )
    task_ids = list(task_ids_result.scalars().all())
    result = await db.execute(
        update(Task)
        .where(Task.pikpak_account_id == account_id)
        .where(Task.file_id == file_id)
        .values(
            status="EXPIRED",
            file_id=None,
            pikpak_task_id=None,
            error_message=error_message,
        )
    )
    await clear_task_links(db, task_ids)
    return int(result.rowcount or 0)


async def _fetch_web_content_link_with_retries(
    driver: PikPakDriver,
    *,
    file_id: str,
    job_id: uuid.UUID,
) -> str:
    """对空直链做短重试；HTTP 异常仍由 get_download_url 抛出。"""
    attempts = max(1, int(settings.DOWNLOAD_WEBLINK_RETRY_ATTEMPTS))
    delay = max(0.2, float(settings.DOWNLOAD_WEBLINK_RETRY_DELAY_SECONDS))
    for attempt in range(1, attempts + 1):
        link = (await driver.get_download_url(file_id) or "").strip()
        if link:
            if attempt > 1:
                log.warning(
                    "download_link_ok_after_retry job_id=%s file_id=%s attempt=%s link_len=%s",
                    job_id,
                    file_id,
                    attempt,
                    len(link),
                )
            else:
                log.info(
                    "download_link_ok job_id=%s file_id=%s link_len=%s",
                    job_id,
                    file_id,
                    len(link),
                )
            return link
        log.warning(
            "download_link_empty_body job_id=%s file_id=%s attempt=%s/%s next_sleep_s=%s",
            job_id,
            file_id,
            attempt,
            attempts,
            delay if attempt < attempts else 0,
        )
        if attempt < attempts:
            await asyncio.sleep(delay)
    return ""


async def _build_folder_download_json_files(
    driver: PikPakDriver,
    *,
    candidates: list[dict[str, Any]],
    primary_file_id: str,
    primary_url: str,
    job_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """为文件夹离线结果组装多文件直链列表（主文件已带 primary_url）。"""
    files_out: list[dict[str, Any]] = []
    for c in candidates:
        fid = str(c.get("id") or "")
        if not fid:
            continue
        name = str(c.get("name") or "")
        size = int(c.get("size") or 0)
        ep = _parse_episode(name)
        ep_key = _episode_key_str(ep.get("season"), ep.get("episode"))
        if fid == primary_file_id:
            u = primary_url
        else:
            u = (await driver.get_download_url(fid) or "").strip()
            if not u:
                u = await _fetch_web_content_link_with_retries(driver, file_id=fid, job_id=job_id)
        if not u:
            continue
        files_out.append(
            {
                "file_id": fid,
                "name": name,
                "size": size,
                "episode": ep,
                "episode_key": ep_key,
                "url": u,
            }
        )
    return files_out


def _cached_rows_to_payload_items(rows: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for r in rows:
        season = int(r.episode_season) if r.episode_season is not None else None
        episode = int(r.episode_number) if r.episode_number is not None else None
        items.append(
            {
                "file_id": str(r.file_id or ""),
                "name": str(r.name or ""),
                "size": int(r.size or 0),
                "episode": {"season": season, "episode": episode},
                "episode_key": str(r.episode_key or "") or None,
                "url": str(r.url or ""),
                "is_primary": bool(r.is_primary),
            }
        )
    return [x for x in items if x["file_id"] and x["url"]]


async def _validate_cached_url(url: str) -> tuple[bool, Optional[int]]:
    """
    轻量校验缓存直链是否仍可用。
    返回 (ok, status_code)。网络异常时返回 (True, None) —— 保守不回源，避免误伤。
    """
    if not bool(settings.DOWNLOAD_LINK_CACHE_VALIDATE_ON_HIT):
        return (True, None)
    u = (url or "").strip()
    if not u.startswith("http"):
        return (False, None)
    timeout_s = max(0.2, float(settings.DOWNLOAD_LINK_CACHE_VALIDATE_TIMEOUT_SECONDS))
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_s) as client:
            r = await client.get(u, headers={"Range": "bytes=0-0"})
        sc = int(r.status_code)
        if sc in (200, 206):
            return (True, sc)
        if sc in (403, 404, 410):
            return (False, sc)
        if sc >= 400:
            return (False, sc)
        return (True, sc)
    except Exception:  # noqa: BLE001
        return (True, None)


async def _poll_reoffline_outcome(job_id: uuid.UUID) -> tuple[str, Optional[str]]:
    """轮询重新离线结果：completed / failed(err) / timeout / missing。"""
    max_sec = max(30, int(settings.DOWNLOAD_REOFFLINE_WAIT_MAX_SECONDS))
    interval = max(0.5, float(settings.DOWNLOAD_REOFFLINE_POLL_INTERVAL_SECONDS))
    deadline = time.monotonic() + max_sec
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        async with AsyncSessionLocal() as session:
            t = await session.get(Task, job_id)
            if t is None:
                return ("missing", None)
            if t.status == "FAILED":
                return ("failed", (t.error_message or "")[:800])
            if t.status == "COMPLETED" and t.file_id:
                return ("completed", None)
    return ("timeout", None)


async def _reoffline_read_outcome_once(job_id: uuid.UUID) -> tuple[str, Optional[str]]:
    """内联 await worker 结束后单次读库；不再 sleep 轮询（避免与 create_task 并发时状态不同步）。"""
    async with AsyncSessionLocal() as session:
        t = await session.get(Task, job_id)
    if t is None:
        return ("missing", None)
    if t.status == "FAILED":
        return ("failed", (t.error_message or "")[:800])
    if t.status == "COMPLETED" and t.file_id:
        return ("completed", None)
    return ("timeout", None)


async def _reoffline_wait_and_redirect(
    db: AsyncSession,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    log_trigger: str,
):
    """网盘已无文件或直链为空时：按用户等级重新排队离线，等待完成后 302 到直链。"""
    from fastapi.responses import RedirectResponse

    from app.workers.offline_worker import process_offline_task, process_task_async

    user = await db.get(User, user_id)
    task = await db.get(Task, job_id)
    if not user or not task:
        log.warning(
            "reoffline_bad_task job_id=%s user_id=%s trigger=%s",
            job_id,
            user_id,
            log_trigger,
        )
        raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)
    try:
        lt = LinkType(task.link_type)
    except ValueError:
        lt = LinkType.UNKNOWN
    est = await estimate_file_size(task.source_url, lt) if lt == LinkType.HTTP else None
    required = max(int(task.file_size or 0), int(est or 0))
    pool = pool_type_for_user_level(user.level)
    account = await pick_account_for_user(db, user.level, required)
    if account is None:
        log.error(
            "reoffline_no_account job_id=%s pool=%s trigger=%s",
            job_id,
            pool,
            log_trigger,
        )
        raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)
    task.pikpak_account_id = account.id
    task.status = "PENDING"
    task.file_id = None
    task.pikpak_task_id = None
    task.progress = 0
    task.completed_at = None
    task.expire_at = None
    task.error_message = None
    await db.commit()
    log.warning(
        "reoffline_task_reset_committed job_id=%s user_id=%s trigger=%s pool=%s "
        "pikpak_account_id=%s link_type=%s content_key=%s source_prefix=%s",
        job_id,
        user_id,
        log_trigger,
        pool,
        str(account.id),
        task.link_type,
        task.content_key,
        (task.source_url or "")[:80],
    )
    if settings.RUN_WORKER_INLINE:
        await process_task_async(str(job_id))
        outcome, fail_detail = await _reoffline_read_outcome_once(job_id)
    else:
        process_offline_task.delay(str(job_id))
        outcome, fail_detail = await _poll_reoffline_outcome(job_id)
    if outcome == "completed":
        async with AsyncSessionLocal() as session:
            t_final = await session.get(Task, job_id)
        if (
            t_final
            and t_final.status == "COMPLETED"
            and t_final.file_id
            and t_final.pikpak_account_id
        ):
            acc = await db.get(PikPakAccount, t_final.pikpak_account_id)
            if not acc:
                log.error(
                    "reoffline_final_missing_account job_id=%s account_id=%s trigger=%s",
                    job_id,
                    t_final.pikpak_account_id,
                    log_trigger,
                )
                raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)
            link = ""
            driver_refresh_out: Optional[str] = None
            for auth_round in range(_PIKPAK_TOKEN_AUTH_RETRIES):
                if auth_round:
                    await db.refresh(acc)
                pwd = decrypt_secret(acc.password_enc)
                driver = PikPakDriver(
                    email=acc.email,
                    password=pwd,
                    device_id=acc.device_id,
                    refresh_token=acc.refresh_token,
                )
                try:
                    await driver.ensure_token()
                    eff_id, _cand = await driver.resolve_magnet_folder_downloads(
                        str(t_final.file_id),
                        min_size_bytes=int(settings.DOWNLOAD_FOLDER_MIN_SIZE_BYTES),
                        max_candidates=int(settings.DOWNLOAD_FOLDER_MAX_LINK_FILES),
                    )
                    link = await _fetch_web_content_link_with_retries(
                        driver,
                        file_id=eff_id,
                        job_id=job_id,
                    )
                    driver_refresh_out = driver.refresh_token
                    break
                except httpx.HTTPStatusError as e:
                    if _http_error_is_pikpak_invalid_grant(e) and auth_round + 1 < _PIKPAK_TOKEN_AUTH_RETRIES:
                        log.warning(
                            "reoffline_second_fetch_invalid_grant_retry job_id=%s round=%s trigger=%s",
                            job_id,
                            auth_round + 1,
                            log_trigger,
                        )
                        continue
                    log.warning(
                        "reoffline_second_fetch_http job_id=%s http=%s trigger=%s",
                        job_id,
                        e.response.status_code,
                        log_trigger,
                    )
                    raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG) from e
                finally:
                    await driver.aclose()
            if driver_refresh_out is not None and driver_refresh_out != acc.refresh_token:
                acc.refresh_token = driver_refresh_out
                await db.commit()
            if link:
                log.warning(
                    "reoffline_success_after_stale job_id=%s user_id=%s trigger=%s new_file_id=%s link_len=%s",
                    job_id,
                    user_id,
                    log_trigger,
                    t_final.file_id,
                    len(link),
                )
                return RedirectResponse(link, status_code=302)
        log.warning("reoffline_completed_no_link job_id=%s trigger=%s", job_id, log_trigger)
        raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)
    if outcome == "failed":
        log.warning(
            "reoffline_worker_failed job_id=%s user_id=%s trigger=%s error=%s",
            job_id,
            user_id,
            log_trigger,
            fail_detail or "",
        )
        raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)
    log.warning(
        "reoffline_poll_timeout_or_missing job_id=%s user_id=%s outcome=%s trigger=%s",
        job_id,
        user_id,
        outcome,
        log_trigger,
    )
    raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)


async def _redirect_to_provider_download(
    *,
    db: AsyncSession,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    return_json: bool = False,
    resolve_all_folder_links: bool = False,
):
    result = await db.execute(select(Task).where(Task.id == job_id, Task.user_id == user_id))
    task = result.scalar_one_or_none()
    if task is None:
        log.warning("download_reject job_id=%s user_id=%s reason=no_task", job_id, user_id)
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    if task.status != "COMPLETED" or not task.file_id or not task.pikpak_account_id:
        log.warning(
            "download_reject job_id=%s user_id=%s reason=not_ready status=%s has_file_id=%s",
            job_id,
            user_id,
            task.status,
            bool(task.file_id),
        )
        raise HTTPException(status_code=404, detail="任务未完成或不可用")

    cached_rows = await get_valid_task_links(db, task.id)
    if cached_rows:
        cached_items = _cached_rows_to_payload_items(cached_rows)
        primary = next((x for x in cached_items if x.get("is_primary")), cached_items[0] if cached_items else None)
        if primary and isinstance(primary.get("url"), str) and primary["url"].strip():
            ok, sc = await _validate_cached_url(primary["url"].strip())
            if not ok:
                log.warning(
                    "download_cache_invalid_refresh job_id=%s user_id=%s http=%s",
                    job_id,
                    user_id,
                    sc,
                )
                await clear_task_links(db, [task.id])
                await db.commit()
                cached_rows = []
                cached_items = []
                primary = None
            else:
                log.info(
                    "download_cache_valid job_id=%s user_id=%s http=%s",
                    job_id,
                    user_id,
                    sc,
                )
        if primary and isinstance(primary.get("url"), str) and primary["url"].strip():
            if return_json:
                body: dict[str, Any] = {
                    "url": primary["url"].strip(),
                    "file_id": str(primary.get("file_id") or task.file_id),
                }
                body["files"] = [
                    {
                        "file_id": str(x.get("file_id") or ""),
                        "name": str(x.get("name") or ""),
                        "size": int(x.get("size") or 0),
                        "episode": x.get("episode"),
                        "episode_key": x.get("episode_key"),
                        "url": str(x.get("url") or ""),
                    }
                    for x in cached_items
                ]
                body["items"] = [
                    {
                        "file_id": str(x.get("file_id") or ""),
                        "name": str(x.get("name") or ""),
                        "size": int(x.get("size") or 0),
                        "episode": x.get("episode"),
                        "episode_key": x.get("episode_key"),
                        "url": str(x.get("url") or ""),
                    }
                    for x in cached_items
                ]
                log.info("download_cache_hit_json job_id=%s user_id=%s count=%s", job_id, user_id, len(cached_items))
                return JSONResponse(body)
            log.info("download_cache_hit_redirect job_id=%s user_id=%s", job_id, user_id)
            return RedirectResponse(primary["url"].strip(), status_code=302)

    now = datetime.now(timezone.utc)
    expire_at = _as_utc(task.expire_at) if task.expire_at else None
    # 只要 PikPak 文件仍存在（file_id 有效），任务不应因时间自动失效。
    # 直链是否过期由 PikPak 实时返回决定，取回时按需重新取链即可。
    if expire_at and expire_at < now:
        log.info(
            "download_link_expire_hint_passed job_id=%s expire_at=%s",
            job_id,
            expire_at.isoformat(),
        )

    account = await db.get(PikPakAccount, task.pikpak_account_id)
    if not account:
        log.warning("download_reject job_id=%s reason=no_account", job_id)
        raise HTTPException(status_code=404, detail="关联账号不存在")

    completed_at = _as_utc(task.completed_at) if task.completed_at else None
    age_sec = (now - completed_at).total_seconds() if completed_at else None
    log.info(
        "download_start job_id=%s user_id=%s file_id=%s account_id=%s pool=%s "
        "completed_at=%s age_sec=%s content_key=%s file_name=%s",
        job_id,
        user_id,
        task.file_id,
        task.pikpak_account_id,
        account.pool_type,
        completed_at.isoformat() if completed_at else None,
        age_sec,
        task.content_key,
        (task.file_name or "")[:120],
    )

    stale_fid = task.file_id
    stale_aid = task.pikpak_account_id
    link = ""
    driver_refresh_out: Optional[str] = None
    effective_file_id = task.file_id
    folder_files_json: Optional[list[dict[str, Any]]] = None
    folder_files_all: Optional[list[dict[str, Any]]] = None
    for auth_round in range(_PIKPAK_TOKEN_AUTH_RETRIES):
        if auth_round:
            await db.refresh(account)
        password = decrypt_secret(account.password_enc)
        driver = PikPakDriver(
            email=account.email,
            password=password,
            device_id=account.device_id,
            refresh_token=account.refresh_token,
        )
        try:
            await driver.ensure_token()
            # 若 file_id 是文件夹：枚举叶子文件 → 过滤/智能阈值 → 选主文件
            folder_meta = await driver.get_file_dict(task.file_id)
            folder_candidates: list[dict[str, Any]] = []
            folder_all_items: list[dict[str, Any]] = []
            if str(folder_meta.get("kind") or "") == "drive#folder" and int(settings.DOWNLOAD_FOLDER_MIN_SIZE_BYTES) >= 0:
                leaves = await driver.list_folder_leaf_files_meta(task.file_id)
                video = [x for x in leaves if _looks_like_video_file(str(x.get("name") or "")) and not _matches_hide_keywords(str(x.get("name") or ""))]
                sizes = [int(x.get("size") or 0) for x in video]
                min_b = int(settings.DOWNLOAD_FOLDER_MIN_SIZE_BYTES)
                if bool(settings.DOWNLOAD_FOLDER_SMART_MIN_SIZE):
                    min_b = _smart_min_size_bytes(sizes)
                eligible = [x for x in video if int(x.get("size") or 0) >= min_b]
                pool = eligible if eligible else video
                # 先按集数/末尾数字/A-Z 做强一致排序；主文件默认取最小集数（更贴近“从第1集开始播放”）
                pool.sort(key=lambda x: _episode_sort_key(str(x.get("name") or "")))
                folder_all_items = pool
                if resolve_all_folder_links:
                    folder_candidates = pool
                else:
                    folder_candidates = pool[: int(settings.DOWNLOAD_FOLDER_MAX_LINK_FILES)]
                if folder_all_items:
                    effective_file_id = str(folder_all_items[0]["id"])
                    log.info(
                        "download_folder_pick job_id=%s root=%s primary=%s all=%s cand=%s min_b=%s smart=%s",
                        job_id,
                        task.file_id,
                        effective_file_id,
                        len(folder_all_items),
                        len(folder_candidates),
                        min_b,
                        bool(settings.DOWNLOAD_FOLDER_SMART_MIN_SIZE),
                    )
            link = await _fetch_web_content_link_with_retries(
                driver, file_id=effective_file_id, job_id=job_id
            )
            driver_refresh_out = driver.refresh_token
            if return_json and folder_candidates and link:
                folder_files_json = await _build_folder_download_json_files(
                    driver,
                    candidates=folder_candidates,
                    primary_file_id=effective_file_id,
                    primary_url=link,
                    job_id=job_id,
                )
                # 给 EMBY/客户端：返回展开后“可见文件”数组（已过滤/排序），用于精确匹配每一集
                def _item_episode_key(name: str) -> Optional[str]:
                    ep2 = _parse_episode(name)
                    return _episode_key_str(ep2.get("season"), ep2.get("episode"))

                # items：返回“全部候选”（不生成全部直链），供前端滚动展示与按需取链
                all_items = folder_all_items or folder_candidates
                folder_files_all = [
                    {
                        "file_id": str(x.get("id") or ""),
                        "name": str(x.get("name") or ""),
                        "size": int(x.get("size") or 0),
                        "episode": _parse_episode(str(x.get("name") or "")),
                        "episode_key": _item_episode_key(str(x.get("name") or "")),
                    }
                    for x in all_items
                    if str(x.get("id") or "")
                ]
            break
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            log.warning(
                "download_link_http job_id=%s file_id=%s http=%s body_snip=%s",
                job_id,
                effective_file_id,
                code,
                (e.response.text or "")[:240].replace("\n", " "),
            )
            if code in (404, 410) and stale_fid and stale_aid:
                rows = await _invalidate_tasks_for_stale_file(
                    db,
                    account_id=stale_aid,
                    file_id=stale_fid,
                    error_message=f"PikPak 返回 HTTP {code}，文件已不存在或已删除",
                )
                await db.commit()
                log.warning(
                    "download_stale_reoffline job_id=%s user_id=%s trigger=http_%s invalidate_rows=%s",
                    job_id,
                    user_id,
                    code,
                    rows,
                )
                return await _reoffline_wait_and_redirect(
                    db,
                    job_id,
                    user_id,
                    log_trigger=f"pikpak_file_http_{code}",
                )
            if _http_error_is_pikpak_invalid_grant(e) and auth_round + 1 < _PIKPAK_TOKEN_AUTH_RETRIES:
                log.warning(
                    "download_link_invalid_grant_retry job_id=%s round=%s/%s",
                    job_id,
                    auth_round + 1,
                    _PIKPAK_TOKEN_AUTH_RETRIES,
                )
                continue
            log.warning(
                "download_link_http_no_stale_path job_id=%s http=%s",
                job_id,
                code,
            )
            raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG) from e
        finally:
            await driver.aclose()

    if driver_refresh_out is not None and driver_refresh_out != account.refresh_token:
        account.refresh_token = driver_refresh_out
        await db.commit()

    if not link:
        grace = timedelta(minutes=max(1, int(settings.DOWNLOAD_RECENT_COMPLETE_GRACE_MINUTES)))
        if completed_at and (now - completed_at) < grace:
            log.warning(
                "download_empty_grace_no_reoffline job_id=%s age_sec=%s grace_min=%s",
                job_id,
                (now - completed_at).total_seconds(),
                int(grace.total_seconds() // 60),
            )
            raise HTTPException(
                status_code=503,
                detail="直链生成中，请稍后再点击取回",
            )
        if stale_fid and stale_aid:
            rows = await _invalidate_tasks_for_stale_file(
                db,
                account_id=stale_aid,
                file_id=stale_fid,
                error_message="PikPak 未返回下载直链（重试后仍为空），按失效处理",
            )
            await db.commit()
            log.warning(
                "download_stale_reoffline job_id=%s user_id=%s trigger=empty_web_content_after_retries "
                "invalidate_rows=%s",
                job_id,
                user_id,
                rows,
            )
            return await _reoffline_wait_and_redirect(
                db,
                job_id,
                user_id,
                log_trigger="empty_web_content_link",
            )
        raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)
    log.info(
        "download_redirect_%s job_id=%s user_id=%s link_len=%s file_id=%s",
        "json" if return_json else "302",
        job_id,
        user_id,
        len(link),
        effective_file_id,
    )
    # 成功取链后刷新一个“提示性”过期时间（非强一致），用于后台/缓存策略与排障。
    task.expire_at = now + timedelta(hours=settings.FILE_TTL_HOURS)
    cache_records: list[dict[str, Any]] = []
    if folder_files_json:
        for idx, it in enumerate(folder_files_json):
            rec = dict(it)
            rec["is_primary"] = str(it.get("file_id") or "") == str(effective_file_id or "")
            rec["sort_index"] = idx
            cache_records.append(rec)
    elif effective_file_id and link:
        ep = _parse_episode(str(task.file_name or ""))
        cache_records.append(
            {
                "file_id": str(effective_file_id),
                "name": str(task.file_name or ""),
                "size": int(task.file_size or 0),
                "episode": ep,
                "episode_key": _episode_key_str(ep.get("season"), ep.get("episode")),
                "url": link,
                "is_primary": True,
                "sort_index": 0,
            }
        )
    await replace_task_links(db, task.id, cache_records)
    await db.commit()
    if return_json:
        body: dict[str, Any] = {"url": link, "file_id": effective_file_id}
        if folder_files_json:
            body["files"] = folder_files_json
        if folder_files_all:
            body["items"] = folder_files_all
        return JSONResponse(body)
    return RedirectResponse(link, status_code=302)


async def _find_reusable_completed_task(db: AsyncSession, content_key: str) -> Optional[Task]:
    """Prefer FREE pool hit, then PREMIUM; newest completion first."""
    now = datetime.now(timezone.utc)
    pool_rank = case((PikPakAccount.pool_type == "FREE", 0), else_=1)
    stmt = (
        select(Task)
        .join(PikPakAccount, Task.pikpak_account_id == PikPakAccount.id)
        .where(Task.status == "COMPLETED")
        .where(Task.content_key == content_key)
        .where(Task.file_id.isnot(None))
        .where(Task.pikpak_account_id.isnot(None))
        .where(or_(Task.expire_at.is_(None), Task.expire_at > now))
        .where(PikPakAccount.status == "ACTIVE")
        .order_by(pool_rank, desc(Task.completed_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _submit_url_task(
    *,
    url: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    user: User,
    forced_link_type: Optional[LinkType] = None,
) -> TaskSubmitResponse:
    url = url.strip()
    link_type = forced_link_type or detect_link_type(url)
    if link_type == LinkType.UNKNOWN:
        raise HTTPException(status_code=400, detail="无法识别的链接类型")

    est = await estimate_file_size(url, link_type)
    ck = content_key_for_url(url, link_type)

    if ck:
        reusable = await _find_reusable_completed_task(db, ck)
        if reusable is not None:
            now = datetime.now(timezone.utc)
            task = Task(
                user_id=user.id,
                pikpak_account_id=reusable.pikpak_account_id,
                source_url=url,
                content_key=ck,
                link_type=link_type.value,
                status="COMPLETED",
                file_id=reusable.file_id,
                file_name=reusable.file_name,
                file_size=int(reusable.file_size or 0),
                progress=100,
                completed_at=now,
                expire_at=now + timedelta(hours=settings.FILE_TTL_HOURS),
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            return TaskSubmitResponse(
                job_id=task.id,
                link_type=link_type.value,
                status=task.status,
                message="命中全站缓存，可直接取回（未消耗离线次数）",
            )

    if user.level == "FREE" and est is not None and est > settings.FREE_MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "FILE_TOO_LARGE",
                "message": "文件大小超过 6GB，请升级 VIP 账户",
                "estimated_size": est,
                "limit": settings.FREE_MAX_FILE_SIZE_BYTES,
            },
        )

    pool = pool_type_for_user_level(user.level)
    account = await pick_account_for_user(db, user.level, est or 0)
    if account is None:
        raise HTTPException(
            status_code=503,
            detail="暂无可用号池账号（请检查号池是否启用、剩次数>0、网盘剩余空间是否足够）",
        )

    task = Task(
        user_id=user.id,
        pikpak_account_id=account.id,
        source_url=url,
        content_key=ck,
        link_type=link_type.value,
        status="PENDING",
        file_size=est or 0,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    from app.workers.offline_worker import process_offline_task, process_task_async

    if settings.RUN_WORKER_INLINE:
        background_tasks.add_task(process_task_async, str(task.id))
    else:
        process_offline_task.delay(str(task.id))

    return TaskSubmitResponse(
        job_id=task.id,
        link_type=link_type.value,
        status=task.status,
        message="任务已提交，正在排队",
    )


@router.post("", response_model=TaskSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_task(
    body: TaskCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskSubmitResponse:
    if detect_link_type(body.url.strip()) == LinkType.TORRENT:
        raise HTTPException(
            status_code=400,
            detail="请使用 /api/v1/tasks/upload-torrent 上传 .torrent 文件",
        )
    return await _submit_url_task(
        url=body.url,
        background_tasks=background_tasks,
        db=db,
        user=user,
    )


@router.post("/upload-torrent", response_model=TaskSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_torrent(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskSubmitResponse:
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(status_code=400, detail="仅支持 .torrent 文件")

    suffix = ".torrent"
    tmp_dir = "/tmp/torrents"
    os.makedirs(tmp_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=suffix, dir=tmp_dir, delete=False) as fp:
        temp_path = fp.name
        content = await file.read()
        fp.write(content)

    try:
        parsed = parse_torrent_file(temp_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"解析种子失败: {str(exc)[:300]}") from exc
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    size = int(parsed.get("size", 0) or 0)
    if user.level == "FREE" and size > settings.FREE_MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "FILE_TOO_LARGE",
                "message": "文件大小超过 6GB，请升级 VIP 账户",
                "estimated_size": size,
                "limit": settings.FREE_MAX_FILE_SIZE_BYTES,
            },
        )

    resp = await _submit_url_task(
        url=str(parsed["magnet"]),
        background_tasks=background_tasks,
        db=db,
        user=user,
        forced_link_type=LinkType.TORRENT,
    )
    task = await db.get(Task, resp.job_id)
    if task is not None:
        task.file_name = str(parsed.get("name") or task.file_name or "")
        if size > 0:
            task.file_size = size
        await db.commit()

    return resp


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskListResponse:
    result = await db.execute(
        select(Task).where(Task.user_id == user.id).order_by(Task.created_at.desc())
    )
    rows = list(result.scalars().all())
    out: list[TaskListItem] = []
    for t in rows:
        item = TaskListItem(
            job_id=str(t.id),
            file_name=t.file_name,
            file_size=int(t.file_size or 0),
            status=t.status,
            progress=int(t.progress or 0),
            download_url=f"/api/v1/download/{t.id}" if t.status == "COMPLETED" else None,
            created_at=t.created_at.isoformat() if t.created_at else None,
            error_message=(t.error_message or None),
        )
        out.append(item)
    return TaskListResponse(tasks=out, total=len(out))


@router.delete("/cleanup", response_model=TaskDeleteResponse)
async def cleanup_tasks(
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskDeleteResponse:
    keep_statuses = ("PENDING", "SUBMITTED", "DOWNLOADING", "COMPLETED")
    stmt = delete(Task).where(Task.user_id == user.id, Task.status.not_in(keep_statuses))
    result = await db.execute(stmt)
    await db.commit()
    return TaskDeleteResponse(deleted=result.rowcount or 0)


@router.delete("/{job_id}", response_model=TaskDeleteResponse)
async def delete_task(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskDeleteResponse:
    stmt = delete(Task).where(Task.id == job_id, Task.user_id == user.id)
    result = await db.execute(stmt)
    await db.commit()
    deleted = result.rowcount or 0
    if deleted == 0:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskDeleteResponse(deleted=deleted)


@router.post("/{job_id}/invalidate-link-cache")
async def invalidate_task_link_cache(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    客户端兜底：播放/下载发现直链失效后，上报让后端清缓存。
    下一次复制链接/下载/M3U 会自动回源 PikPak 刷新并写入新缓存。
    """
    result = await db.execute(select(Task).where(Task.id == job_id, Task.user_id == user.id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await clear_task_links(db, [task.id])
    await db.commit()
    return {"ok": True}


@router.post("/{job_id}/download-link", response_model=TaskDownloadLinkResponse)
async def create_download_link(
    job_id: uuid.UUID,
    resolve_provider: bool = Query(
        False,
        description="为 true 时同步向 PikPak 取真实直链并填入 provider_url；可直接打开该链接，无需再跟随 Hub 302",
    ),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskDownloadLinkResponse:
    result = await db.execute(select(Task).where(Task.id == job_id, Task.user_id == user.id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "COMPLETED":
        raise HTTPException(status_code=400, detail="任务未完成，无法下载")
    ttl = max(10, int(settings.DOWNLOAD_SIGNED_LINK_EXPIRE_SECONDS))
    jti = str(uuid.uuid4())
    token = create_download_token(str(user.id), str(job_id), jti, ttl)
    rel = f"/api/v1/download/signed/{token}"
    provider_url: Optional[str] = None
    if resolve_provider:
        try:
            resp = await _redirect_to_provider_download(
                db=db,
                job_id=job_id,
                user_id=user.id,
                return_json=True,
            )
            if isinstance(resp, JSONResponse):
                try:
                    blob = resp.body
                    if isinstance(blob, memoryview):
                        blob = blob.tobytes()
                    payload = json.loads(blob.decode())
                    pu = payload.get("url")
                    if isinstance(pu, str) and pu.strip():
                        provider_url = pu.strip()
                    if not provider_url and isinstance(payload.get("files"), list):
                        for item in payload["files"]:
                            if isinstance(item, dict):
                                u = item.get("url")
                                if isinstance(u, str) and u.strip().startswith("http"):
                                    provider_url = u.strip()
                                    break
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError):
                    provider_url = None
            elif isinstance(resp, RedirectResponse):
                log.info(
                    "download_link_resolve_redirect_no_json job_id=%s user_id=%s",
                    job_id,
                    user.id,
                )
        except HTTPException as exc:
            # 直链尚未就绪 / 重新离线中等：仍返回签名 url，由客户端用 ?format=json 或稍后重试
            log.warning(
                "download_link_resolve_provider_skipped job_id=%s user_id=%s http=%s detail=%s",
                job_id,
                user.id,
                exc.status_code,
                (exc.detail or "")[:200],
            )
    return TaskDownloadLinkResponse(url=rel, expires_in=ttl, provider_url=provider_url)


async def _driver_fetch_file_link(
    db: AsyncSession,
    account: PikPakAccount,
    file_id: str,
    job_id: uuid.UUID,
) -> str:
    """单次回源 PikPak 取链，带进程内短缓存。"""
    aid = str(account.id)
    mem = get_cached_file_link(aid, file_id)
    if mem and not pikpak_url_is_expired(mem):
        log.info("file_link_memory_hit job_id=%s file_id=%s", job_id, file_id)
        return mem

    password = decrypt_secret(account.password_enc)
    driver = PikPakDriver(
        email=account.email,
        password=password,
        device_id=account.device_id,
        refresh_token=account.refresh_token,
    )
    try:
        await driver.ensure_token()
        url = await _fetch_web_content_link_with_retries(driver, file_id=file_id, job_id=job_id)
    finally:
        account.refresh_token = driver.refresh_token or account.refresh_token
        await db.commit()
        await driver.aclose()
    if not url:
        raise HTTPException(status_code=503, detail="直链生成中，请稍后重试")
    set_cached_file_link(aid, file_id, url)
    return url


async def _resolve_play_url_for_task(
    db: AsyncSession,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    file_index: int,
) -> TaskPlayUrlResponse:
    result = await db.execute(select(Task).where(Task.id == job_id, Task.user_id == user_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "COMPLETED" or not task.pikpak_account_id:
        raise HTTPException(status_code=400, detail="任务未完成，无法取链")

    cached_rows = await get_valid_task_links(db, task.id)
    target_row = cached_rows[file_index] if cached_rows and 0 <= file_index < len(cached_rows) else None
    if target_row:
        cached_url = (target_row.url or "").strip()
        if cached_url and not pikpak_url_is_expired(cached_url):
            log.info(
                "play_url_db_cache_hit job_id=%s user_id=%s file_index=%s file_id=%s",
                job_id,
                user_id,
                file_index,
                target_row.file_id,
            )
            return TaskPlayUrlResponse(
                url=cached_url,
                file_id=str(target_row.file_id or ""),
                from_cache=True,
                expires_at=pikpak_url_expire_epoch(cached_url),
            )

    file_id = str(target_row.file_id if target_row and target_row.file_id else (task.file_id or ""))
    if not file_id:
        log.info("play_url_cold_populate job_id=%s user_id=%s file_index=%s", job_id, user_id, file_index)
        resp = await _redirect_to_provider_download(
            db=db,
            job_id=job_id,
            user_id=user_id,
            return_json=True,
        )
        if isinstance(resp, JSONResponse):
            payload = json.loads(resp.body.decode())
            files = payload.get("files") if isinstance(payload.get("files"), list) else []
            if files and 0 <= file_index < len(files):
                it = files[file_index]
                if isinstance(it, dict):
                    u = str(it.get("url") or "").strip()
                    fid = str(it.get("file_id") or "").strip()
                    if u and fid:
                        return TaskPlayUrlResponse(
                            url=u,
                            file_id=fid,
                            from_cache=False,
                            expires_at=pikpak_url_expire_epoch(u),
                        )
            u = str(payload.get("url") or "").strip()
            fid = str(payload.get("file_id") or "").strip()
            if u and fid:
                return TaskPlayUrlResponse(
                    url=u,
                    file_id=fid,
                    from_cache=False,
                    expires_at=pikpak_url_expire_epoch(u),
                )
        raise HTTPException(status_code=503, detail=_USER_DOWNLOAD_RETRY_MSG)

    account = await db.get(PikPakAccount, task.pikpak_account_id)
    if not account:
        raise HTTPException(status_code=404, detail="关联账号不存在")
    url = await _driver_fetch_file_link(db, account, file_id, job_id)

    if cached_rows:
        items = _cached_rows_to_payload_items(cached_rows)
        updated = False
        for it in items:
            if str(it.get("file_id") or "") == file_id:
                it["url"] = url
                updated = True
                break
        if not updated and 0 <= file_index < len(items):
            items[file_index]["url"] = url
        if items:
            await replace_task_links(db, task.id, items)
            await db.commit()

    log.info(
        "play_url_refreshed job_id=%s user_id=%s file_index=%s file_id=%s link_len=%s",
        job_id,
        user_id,
        file_index,
        file_id,
        len(url),
    )
    return TaskPlayUrlResponse(
        url=url,
        file_id=file_id,
        from_cache=False,
        expires_at=pikpak_url_expire_epoch(url),
    )


@router.get("/{job_id}/play-url", response_model=TaskPlayUrlResponse)
async def resolve_task_play_url(
    job_id: uuid.UUID,
    file_index: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskPlayUrlResponse:
    """
    播放专用合并取链：优先 DB/内存缓存，仅在签名过期或缺失时单次回源 PikPak。
    LiteEmby PlaybackInfo 应只调用此接口，避免 download/json + file-link 双往返。
    """
    return await _resolve_play_url_for_task(db, job_id, user.id, file_index)


@router.get("/{job_id}/file-link")
async def resolve_task_file_link(
    job_id: uuid.UUID,
    file_id: str = Query(..., min_length=3),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    用于前端“文件列表”按需取链：给定任务与子文件 id，返回该文件的真实直链（JSON）。
    注意：这会向 PikPak 发请求，建议前端点选某一集时再调用。
    """
    result = await db.execute(select(Task).where(Task.id == job_id, Task.user_id == user.id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "COMPLETED" or not task.pikpak_account_id:
        raise HTTPException(status_code=400, detail="任务未完成，无法取链")
    account = await db.get(PikPakAccount, task.pikpak_account_id)
    if not account:
        raise HTTPException(status_code=404, detail="关联账号不存在")
    url = await _driver_fetch_file_link(db, account, file_id, job_id)
    return JSONResponse({"url": url, "file_id": file_id})


@router.get("/{job_id}", response_model=TaskDetail)
async def task_detail(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TaskDetail:
    result = await db.execute(select(Task).where(Task.id == job_id, Task.user_id == user.id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    pool_type: Optional[str] = None
    if task.pikpak_account_id:
        from app.models.pikpak_account import PikPakAccount

        acc = await db.get(PikPakAccount, task.pikpak_account_id)
        if acc:
            pool_type = acc.pool_type
    return TaskDetail(
        job_id=task.id,
        source_url=task.source_url,
        link_type=task.link_type,
        file_name=task.file_name,
        file_size=int(task.file_size or 0),
        status=task.status,
        progress=int(task.progress or 0),
        pool_type=pool_type,
        created_at=task.created_at,
        completed_at=task.completed_at,
    )


@download_router.get("/download/{job_id}")
async def download_redirect(
    job_id: uuid.UUID,
    fmt: Optional[str] = Query(None, alias="format"),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    log.info("download_entry_bearer job_id=%s user_id=%s", job_id, user.id)
    fmt2 = (fmt or "").lower()
    want_json = fmt2 in ("json", "1", "true")
    want_m3u = fmt2 in ("m3u", "m3u8", "playlist")
    try:
        if want_m3u:
            resp = await _redirect_to_provider_download(
                db=db,
                job_id=job_id,
                user_id=user.id,
                return_json=True,
                resolve_all_folder_links=True,
            )
            if isinstance(resp, JSONResponse):
                payload = json.loads(resp.body.decode())
                files = payload.get("files") if isinstance(payload, dict) else None
                items = files if isinstance(files, list) else []
                lines = ["#EXTM3U"]
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    u = it.get("url")
                    if not isinstance(u, str) or not u.strip():
                        continue
                    title = str(it.get("name") or "Episode").replace("\n", " ").strip()
                    lines.append(f"#EXTINF:-1,{title}")
                    lines.append(u.strip())
                text = "\n".join(lines) + "\n"
                return PlainTextResponse(text, media_type="application/x-mpegURL")
            raise HTTPException(status_code=503, detail="直链暂不可用，请稍后重试")
        return await _redirect_to_provider_download(db=db, job_id=job_id, user_id=user.id, return_json=want_json)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("download_bearer_failed job_id=%s user_id=%s", job_id, user.id)
        raise HTTPException(status_code=503, detail="取回失败，请稍后重试") from exc


@download_router.get("/download/signed/{token}")
async def download_redirect_by_signed_token(
    token: str,
    fmt: Optional[str] = Query(None, alias="format"),
    db: AsyncSession = Depends(get_session),
):
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if payload.get("type") != "download_once":
        raise HTTPException(status_code=401, detail="无效下载签名")
    sub = payload.get("sub")
    job_id = payload.get("job_id")
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not sub or not job_id or not jti or not exp:
        raise HTTPException(status_code=401, detail="无效下载签名")
    try:
        uid = uuid.UUID(str(sub))
        tid = uuid.UUID(str(job_id))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="无效下载签名") from exc
    _consume_once_jti(str(jti), int(exp))
    log.info(
        "download_entry_signed job_id=%s user_id=%s jti_prefix=%s",
        tid,
        uid,
        str(jti)[:12],
    )
    fmt2 = (fmt or "").lower()
    want_json = fmt2 in ("json", "1", "true")
    want_m3u = fmt2 in ("m3u", "m3u8", "playlist")
    try:
        if want_m3u:
            resp = await _redirect_to_provider_download(
                db=db,
                job_id=tid,
                user_id=uid,
                return_json=True,
                resolve_all_folder_links=True,
            )
            if isinstance(resp, JSONResponse):
                payload = json.loads(resp.body.decode())
                files = payload.get("files") if isinstance(payload, dict) else None
                items = files if isinstance(files, list) else []
                lines = ["#EXTM3U"]
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    u = it.get("url")
                    if not isinstance(u, str) or not u.strip():
                        continue
                    title = str(it.get("name") or "Episode").replace("\n", " ").strip()
                    lines.append(f"#EXTINF:-1,{title}")
                    lines.append(u.strip())
                text = "\n".join(lines) + "\n"
                return PlainTextResponse(text, media_type="application/x-mpegURL")
            raise HTTPException(status_code=503, detail="直链暂不可用，请稍后重试")
        return await _redirect_to_provider_download(db=db, job_id=tid, user_id=uid, return_json=want_json)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("download_signed_failed job_id=%s user_id=%s", tid, uid)
        raise HTTPException(status_code=503, detail="取回失败，请稍后重试") from exc
