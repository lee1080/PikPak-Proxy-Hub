from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx
from sqlalchemy import and_, func, select

from app.config import settings
from app.database import get_session
from app.models.admin_runtime_log import AdminRuntimeLog
from app.models.pikpak_account import PikPakAccount
from app.models.task import Task
from app.models.user import User
from app.routers.deps import require_admin
from app.schemas.account import (
    PikPakAccountAdminCreate,
    PikPakCaptchaTokenGenerateResponse,
    PikPakAccountCleanupDriveResponse,
    PikPakAccountDailyTasksPatchResponse,
    PikPakAccountResetDailyLimitsResponse,
    PikPakAccountSyncFromApiResponse,
    PikPakAccountVerifyAllResponse,
    PikPakAccountVerifyItem,
    PikPakSigninPrecheckRequest,
    PikPakSigninPrecheckResponse,
    ProxyTestResponse,
    RuntimeConfigPatch,
    RuntimeConfigResponse,
)
from app.services.daily_quota_reset import reset_free_daily_limits, today_reset_key
from app.schemas.task_admin import TaskAdminListItem, TaskAdminListResponse
from app.schemas.user import (
    UserAdminBatchDeleteRequest,
    UserAdminPatch,
    UserAdminResetPasswordRequest,
    UserPublic,
)
from app.services.admin_log_buffer import get_admin_log_buffer
from app.services.admin_log_db import purge_expired_admin_runtime_logs
from app.services.download_link_cache import clear_task_links
from app.services.encryption import decrypt_secret, encrypt_secret
from app.services.pikpak_driver import PikPakDriver
from app.utils.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

_LIMITED_LEVELS = ("FREE", "VIP", "ADMIN")


@router.get("/users", response_model=dict)
async def list_users(
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> dict:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    rows = list(result.scalars().all())
    return {
        "users": [
            {
                "id": str(u.id),
                "username": u.username,
                "email": u.email,
                "level": u.level,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ],
        "total": len(rows),
    }


@router.get("/logs", response_model=dict)
async def admin_runtime_logs(
    limit: int = Query(200, ge=1, le=3000),
    level: Optional[str] = Query(
        None,
        description="可选：逗号分隔，如 INFO,WARNING,ERROR",
    ),
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> dict:
    """运行日志：ADMIN_LOG_RETENTION_DAYS>0 时读数据库并按天清理；否则仅内存环。"""
    buf = get_admin_log_buffer()
    retention = int(settings.ADMIN_LOG_RETENTION_DAYS)
    if retention > 0:
        stmt = select(AdminRuntimeLog).order_by(AdminRuntimeLog.created_at.desc()).limit(limit)
        if level:
            allow = {p.strip().upper() for p in str(level).split(",") if p.strip()}
            stmt = (
                select(AdminRuntimeLog)
                .where(func.upper(AdminRuntimeLog.level).in_(allow))
                .order_by(AdminRuntimeLog.created_at.desc())
                .limit(limit)
            )
        result = await db.execute(stmt)
        rows = list(result.scalars().all())
        lines = [
            {
                "ts": r.created_at.isoformat() if r.created_at else "",
                "level": r.level,
                "logger": r.logger,
                "message": r.message,
            }
            for r in rows
        ]
        return {
            "lines": lines,
            "source": "database",
            "retention_days": retention,
            "memory_buffered": buf.size(),
            "memory_max": int(settings.ADMIN_LOG_BUFFER_MAX),
        }

    lines = buf.tail(limit)
    if level:
        allow = {p.strip().upper() for p in str(level).split(",") if p.strip()}
        lines = [row for row in lines if str(row.get("level", "")).upper() in allow]
    return {
        "lines": lines,
        "source": "memory",
        "retention_days": 0,
        "memory_buffered": buf.size(),
        "memory_max": int(settings.ADMIN_LOG_BUFFER_MAX),
    }


_POOL_TYPES = ("FREE", "PREMIUM")

_admin_log = logging.getLogger(__name__)


def _int_from_quota_json(val: object) -> int:
    if val is None or isinstance(val, bool):
        return 0
    if isinstance(val, int):
        return max(0, val)
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return max(0, int(val.strip()))
    return 0


def _coerce_nonneg_slot(val: object) -> Optional[int]:
    if isinstance(val, bool):
        return None
    if isinstance(val, int) and 0 <= val <= 9999:
        return val
    if isinstance(val, str) and val.strip().isdigit():
        n = int(val.strip())
        if 0 <= n <= 9999:
            return n
    return None


def _pikpak_error_snippet(exc: BaseException, limit: int = 320) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            data = exc.response.json()
            if isinstance(data, dict):
                return str(
                    data.get("message")
                    or data.get("error_description")
                    or data.get("error")
                    or data
                )[:limit]
        except Exception:  # noqa: BLE001
            pass
        try:
            return (exc.response.text or str(exc.response.status_code))[:limit]
        except Exception:  # noqa: BLE001
            return str(exc.response.status_code)
    return str(exc)[:limit]


async def _verify_pikpak_account_row(db, acc: PikPakAccount) -> PikPakAccountVerifyItem:
    """
    依次验证单账号：先 ensure_token + quota；失败则密码重新登录；
    仍失败则设为 INACTIVE。成功则写回 refresh_token / 用量并保持 ACTIVE。
    """
    pwd = decrypt_secret(acc.password_enc)
    driver = PikPakDriver(
        email=acc.email,
        password=pwd,
        device_id=acc.device_id,
        refresh_token=acc.refresh_token,
    )
    outcome = "ok"
    message = "令牌有效"
    try:
        try:
            await driver.ensure_token()
            quota = await driver.get_quota()
        except Exception as first_exc:  # noqa: BLE001
            driver.access_token = None
            try:
                await driver.login()
                quota = await driver.get_quota()
                outcome = "relogin_ok"
                message = "已重新登录并恢复"
            except Exception as relogin_exc:  # noqa: BLE001
                acc.status = "INACTIVE"
                await db.commit()
                detail = _pikpak_error_snippet(relogin_exc)
                _admin_log.warning(
                    "account_verify_deactivated account_id=%s email=%s err=%s",
                    acc.id,
                    acc.email,
                    detail,
                )
                return PikPakAccountVerifyItem(
                    id=acc.id,
                    email=acc.email,
                    pool_type=acc.pool_type,
                    outcome="deactivated",
                    status=acc.status,
                    message=f"重新登录失败，已自动停用：{detail or '未知错误'}",
                )
            _admin_log.info(
                "account_verify_relogin_ok account_id=%s email=%s first_err=%s",
                acc.id,
                acc.email,
                _pikpak_error_snippet(first_exc, 180),
            )

        acc.refresh_token = driver.refresh_token or acc.refresh_token
        acc.quota_total = int(quota.get("limit") or acc.quota_total or 0)
        acc.quota_used = int(quota.get("usage") or 0)
        acc.status = "ACTIVE"
        acc.last_synced_at = datetime.now(timezone.utc)
        try:
            acc.hub_folder_id = await driver.ensure_pphub_folder_id()
        except Exception:  # noqa: BLE001
            pass
        await db.commit()
        return PikPakAccountVerifyItem(
            id=acc.id,
            email=acc.email,
            pool_type=acc.pool_type,
            outcome=outcome,
            status=acc.status,
            message=message,
        )
    finally:
        await driver.aclose()


def _parse_offline_remaining_from_about(about: dict) -> Optional[int]:
    """若 PikPak about 中含「离线/保存」剩余次数字段则解析；当前公开结构多为网盘字节配额，常返回 None。"""
    keys = (
        "offline_save_remaining",
        "remaining_save_times",
        "remaining_offline_times",
        "daily_offline_remaining",
        "save_remaining",
    )
    for k in keys:
        v = _coerce_nonneg_slot(about.get(k))
        if v is not None:
            return v
    q = about.get("quota")
    if isinstance(q, dict):
        for k in keys:
            v = _coerce_nonneg_slot(q.get(k))
            if v is not None:
                return v
    quotas = about.get("quotas")
    if isinstance(quotas, list):
        for block in quotas:
            if not isinstance(block, dict):
                continue
            for k in keys:
                v = _coerce_nonneg_slot(block.get(k))
                if v is not None:
                    return v
    return None


def _normalize_optional(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    return s or None


def _validate_proxy(proxy: Optional[str]) -> Optional[str]:
    p = _normalize_optional(proxy)
    if p is None:
        return None
    if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://")):
        raise HTTPException(status_code=400, detail="代理地址必须以 http://、https:// 或 socks5:// 开头")
    return p


def _validate_captcha_sign_profile(v: str) -> str:
    s = str(v).strip().lower()
    if not s:
        raise HTTPException(status_code=400, detail="Captcha 签名策略不能为空")
    if s not in ("android", "web"):
        raise HTTPException(status_code=400, detail="Captcha 签名策略只能是 android 或 web")
    return s


def _validate_fixed_device_id(v: Optional[str]) -> Optional[str]:
    s = _normalize_optional(v)
    if s is None:
        return None
    # 兼容用户输入 UUID（含 -），统一归一为 PikPak 常见的 32 位十六进制风格
    normalized = s.replace("-", "").strip()
    if len(normalized) < 16 or len(normalized) > 64:
        raise HTTPException(status_code=400, detail="固定 device_id 长度需在 16-64 之间（去掉短横线后）")
    if not re.fullmatch(r"[A-Za-z0-9_]+", normalized):
        raise HTTPException(status_code=400, detail="固定 device_id 仅允许字母、数字、下划线")
    return normalized


def _runtime_device_id() -> str:
    return settings.PIKPAK_FIXED_DEVICE_ID or uuid.uuid4().hex


@router.get("/runtime-config", response_model=RuntimeConfigResponse)
async def get_runtime_config(_: User = Depends(require_admin)) -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        pikpak_proxy=settings.PIKPAK_PROXY,
        pikpak_captcha_token=settings.PIKPAK_CAPTCHA_TOKEN,
        pikpak_captcha_sign_profile=settings.PIKPAK_CAPTCHA_SIGN_PROFILE,
        pikpak_fixed_device_id=settings.PIKPAK_FIXED_DEVICE_ID,
        admin_log_retention_days=int(settings.ADMIN_LOG_RETENTION_DAYS),
    )


@router.post("/runtime-config/test-proxy", response_model=ProxyTestResponse)
async def test_runtime_proxy(_: User = Depends(require_admin)) -> ProxyTestResponse:
    mode = f"通过代理 {settings.PIKPAK_PROXY}" if settings.PIKPAK_PROXY else "直连（未配置代理）"
    try:
        async with httpx.AsyncClient(proxy=settings.PIKPAK_PROXY, timeout=15.0) as client:
            # 连通性判断：只要能收到 PikPak 域名的 HTTP 响应（即便是 4xx/5xx），说明网络链路可达。
            # 部分 PikPak 接口在未携带特定参数时会返回 501，不代表网络不通。
            resp = await client.get("https://user.mypikpak.com/", follow_redirects=True)
            if 100 <= resp.status_code <= 599:
                return ProxyTestResponse(
                    ok=True,
                    status_code=resp.status_code,
                    message=f"{mode}：链路可达（HTTP {resp.status_code}）",
                )
            return ProxyTestResponse(
                ok=False,
                status_code=resp.status_code,
                message=f"{mode}：请求异常，状态码 {resp.status_code}",
            )
    except Exception as e:  # noqa: BLE001
        return ProxyTestResponse(ok=False, status_code=None, message=f"{mode}：连通失败: {str(e)[:300]}")


@router.post("/runtime-config/precheck-signin", response_model=PikPakSigninPrecheckResponse)
async def precheck_pikpak_signin(
    body: PikPakSigninPrecheckRequest,
    _: User = Depends(require_admin),
) -> PikPakSigninPrecheckResponse:
    device_id = _runtime_device_id()
    try:
        driver = PikPakDriver(email=body.email, password=body.password, device_id=device_id)
    except ImportError as e:
        return PikPakSigninPrecheckResponse(
            ok=False,
            message=f"代理依赖缺失: {str(e)[:300]}",
        )
    try:
        await driver.login()
        return PikPakSigninPrecheckResponse(ok=True, message="登录预检查成功，可尝试入库")
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            data = e.response.json()
            if isinstance(data, dict):
                detail = str(
                    data.get("message")
                    or data.get("error_description")
                    or data.get("error")
                    or data
                )
            else:
                detail = str(data)
        except Exception:
            detail = (e.response.text or "")[:500]
        return PikPakSigninPrecheckResponse(
            ok=False,
            message=f"登录预检查失败({e.response.status_code}): {detail}",
        )
    except Exception as e:  # noqa: BLE001
        return PikPakSigninPrecheckResponse(ok=False, message=f"登录预检查失败: {str(e)[:300]}")
    finally:
        await driver.aclose()


@router.post("/runtime-config/generate-captcha-token", response_model=PikPakCaptchaTokenGenerateResponse)
async def generate_runtime_captcha_token(
    body: PikPakSigninPrecheckRequest,
    _: User = Depends(require_admin),
) -> PikPakCaptchaTokenGenerateResponse:
    device_id = _runtime_device_id()
    try:
        driver = PikPakDriver(email=body.email, password=body.password, device_id=device_id)
    except ImportError as e:
        return PikPakCaptchaTokenGenerateResponse(
            ok=False,
            device_id=device_id,
            message=f"代理依赖缺失: {str(e)[:300]}",
        )
    try:
        token = await driver.generate_captcha_token()
        settings.PIKPAK_CAPTCHA_TOKEN = token
        settings.PIKPAK_FIXED_DEVICE_ID = device_id
        return PikPakCaptchaTokenGenerateResponse(
            ok=True,
            captcha_token=token,
            device_id=device_id,
            message="已生成并写入运行时 captcha_token（当前实例已生效）",
        )
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            data = e.response.json()
            detail = str(data)
        except Exception:
            detail = (e.response.text or "")[:500]
        return PikPakCaptchaTokenGenerateResponse(
            ok=False,
            device_id=device_id,
            message=f"获取 captcha_token 失败({e.response.status_code}): {detail}",
        )
    except Exception as e:  # noqa: BLE001
        return PikPakCaptchaTokenGenerateResponse(
            ok=False,
            device_id=device_id,
            message=f"获取 captcha_token 失败: {str(e)[:400]}",
        )
    finally:
        await driver.aclose()


@router.patch("/runtime-config", response_model=RuntimeConfigResponse)
async def patch_runtime_config(
    body: RuntimeConfigPatch,
    _: User = Depends(require_admin),
) -> RuntimeConfigResponse:
    # 仅作用于当前运行实例，重启后以 .env 为准
    if body.pikpak_proxy is not None:
        settings.PIKPAK_PROXY = _validate_proxy(body.pikpak_proxy)
    if body.pikpak_captcha_token is not None:
        settings.PIKPAK_CAPTCHA_TOKEN = _normalize_optional(body.pikpak_captcha_token)
    if body.pikpak_captcha_sign_profile is not None:
        settings.PIKPAK_CAPTCHA_SIGN_PROFILE = _validate_captcha_sign_profile(body.pikpak_captcha_sign_profile)
    if body.pikpak_fixed_device_id is not None:
        settings.PIKPAK_FIXED_DEVICE_ID = _validate_fixed_device_id(body.pikpak_fixed_device_id)
    if body.admin_log_retention_days is not None:
        try:
            n = int(body.admin_log_retention_days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="admin_log_retention_days 须为整数") from None
        settings.ADMIN_LOG_RETENTION_DAYS = max(0, min(n, 3650))
        try:
            await purge_expired_admin_runtime_logs()
        except Exception:
            pass

    return RuntimeConfigResponse(
        pikpak_proxy=settings.PIKPAK_PROXY,
        pikpak_captcha_token=settings.PIKPAK_CAPTCHA_TOKEN,
        pikpak_captcha_sign_profile=settings.PIKPAK_CAPTCHA_SIGN_PROFILE,
        pikpak_fixed_device_id=settings.PIKPAK_FIXED_DEVICE_ID,
        admin_log_retention_days=int(settings.ADMIN_LOG_RETENTION_DAYS),
    )


@router.get("/accounts", response_model=dict)
async def list_accounts(
    db=Depends(get_session),
    _: User = Depends(require_admin),
    pool_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="ACTIVE / INACTIVE / ALL"),
) -> dict:
    filters = []
    if pool_type is not None:
        pool_type = pool_type.strip().upper()
        if pool_type not in _POOL_TYPES:
            raise HTTPException(status_code=400, detail="pool_type 仅支持 FREE / PREMIUM")
        filters.append(PikPakAccount.pool_type == pool_type)

    if status is not None:
        st = status.strip().upper()
        if st in ("ALL", ""):
            pass
        elif st in ("ACTIVE", "INACTIVE"):
            filters.append(PikPakAccount.status == st)
        else:
            raise HTTPException(status_code=400, detail="status 仅支持 ACTIVE / INACTIVE / ALL")

    stmt = select(PikPakAccount)
    if filters:
        stmt = stmt.where(and_(*filters))
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    accounts = []
    for a in rows:
        total = max(int(a.quota_total or 1), 1)
        used = min(int(a.quota_used or 0), total)
        pct = round(used * 100.0 / total, 2)
        accounts.append(
            {
                "id": str(a.id),
                "email": a.email,
                "pool_type": a.pool_type,
                "status": a.status,
                "quota_total": int(a.quota_total),
                "quota_used": int(a.quota_used),
                "quota_percent": pct,
                "daily_tasks_left": int(a.daily_tasks_left),
                "hub_folder_id": a.hub_folder_id,
            },
        )
    return {"accounts": accounts}


@router.post("/accounts/verify-all", response_model=PikPakAccountVerifyAllResponse)
async def verify_all_accounts(
    db=Depends(get_session),
    _: User = Depends(require_admin),
    pool_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="ACTIVE / INACTIVE / ALL"),
) -> PikPakAccountVerifyAllResponse:
    """
    按当前筛选条件依次验证号池账号：令牌可用则同步用量；
    不可用则尝试密码重新登录；仍失败则自动停用（INACTIVE）。
    """
    filters = []
    if pool_type is not None:
        pt = pool_type.strip().upper()
        if pt not in _POOL_TYPES:
            raise HTTPException(status_code=400, detail="pool_type 仅支持 FREE / PREMIUM")
        filters.append(PikPakAccount.pool_type == pt)

    if status is not None:
        st = status.strip().upper()
        if st not in ("ALL", ""):
            if st in ("ACTIVE", "INACTIVE"):
                filters.append(PikPakAccount.status == st)
            else:
                raise HTTPException(status_code=400, detail="status 仅支持 ACTIVE / INACTIVE / ALL")

    stmt = select(PikPakAccount).order_by(PikPakAccount.email)
    if filters:
        stmt = stmt.where(and_(*filters))
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    results: list[PikPakAccountVerifyItem] = []
    ok_count = 0
    relogin_ok_count = 0
    deactivated_count = 0
    for acc in rows:
        item = await _verify_pikpak_account_row(db, acc)
        results.append(item)
        if item.outcome == "ok":
            ok_count += 1
        elif item.outcome == "relogin_ok":
            relogin_ok_count += 1
        elif item.outcome == "deactivated":
            deactivated_count += 1

    return PikPakAccountVerifyAllResponse(
        ok=deactivated_count == 0,
        total=len(results),
        ok_count=ok_count,
        relogin_ok_count=relogin_ok_count,
        deactivated_count=deactivated_count,
        results=results,
    )


@router.post("/accounts/reset-daily-limits", response_model=PikPakAccountResetDailyLimitsResponse)
async def reset_accounts_daily_limits(
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> PikPakAccountResetDailyLimitsResponse:
    """手动将 FREE 池全部账号的剩次数恢复为 FREE_DAILY_TASK_LIMIT（并启用）。"""
    updated = await reset_free_daily_limits(db)
    today = today_reset_key()
    limit = int(settings.FREE_DAILY_TASK_LIMIT)
    return PikPakAccountResetDailyLimitsResponse(
        ok=True,
        updated_count=updated,
        daily_limit=limit,
        reset_date=today,
        message=f"已重置 {updated} 个 FREE 账号，剩次数={limit}（{today}）",
    )


@router.patch(
    "/accounts/{account_id}/daily-tasks-left",
    response_model=PikPakAccountDailyTasksPatchResponse,
)
async def set_account_daily_tasks_left(
    account_id: uuid.UUID,
    daily_tasks_left: int = Query(..., description="FREE≥0；PREMIUM 可用 -1 表示无限"),
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> PikPakAccountDailyTasksPatchResponse:
    acc = await db.get(PikPakAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    pool = (acc.pool_type or "").upper()
    if pool == "FREE":
        if daily_tasks_left < 0:
            raise HTTPException(status_code=400, detail="FREE 池剩次数不能为负数")
        if daily_tasks_left > 999:
            raise HTTPException(status_code=400, detail="剩次数过大（最大 999）")
    elif pool == "PREMIUM":
        if daily_tasks_left < -1 or daily_tasks_left > 9999:
            raise HTTPException(status_code=400, detail="PREMIUM 剩次数须为 -1（无限）或 0～9999")
    else:
        raise HTTPException(status_code=400, detail="未知 pool_type")
    acc.daily_tasks_left = int(daily_tasks_left)
    await db.commit()
    await db.refresh(acc)
    label = "无限" if acc.daily_tasks_left == -1 else str(acc.daily_tasks_left)
    return PikPakAccountDailyTasksPatchResponse(
        ok=True,
        id=acc.id,
        email=acc.email,
        pool_type=acc.pool_type,
        daily_tasks_left=int(acc.daily_tasks_left),
        message=f"已设置剩次数为 {label}",
    )


@router.patch("/accounts/{account_id}/status", response_model=dict)
async def set_account_status(
    account_id: uuid.UUID,
    status: str = Query(..., description="ACTIVE / INACTIVE"),
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> dict:
    st = (status or "").strip().upper()
    if st not in ("ACTIVE", "INACTIVE"):
        raise HTTPException(status_code=400, detail="status 仅支持 ACTIVE / INACTIVE")
    acc = await db.get(PikPakAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    acc.status = st
    await db.commit()
    return {"ok": True, "id": str(acc.id), "status": acc.status}


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def add_account(
    body: PikPakAccountAdminCreate,
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> dict:
    if body.pool_type not in _POOL_TYPES:
        raise HTTPException(status_code=400, detail="无效的 pool_type")
    exists = await db.execute(select(PikPakAccount).where(PikPakAccount.email == body.email))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该 PikPak 邮箱已存在")

    device_id = _runtime_device_id()
    try:
        driver = PikPakDriver(email=body.email, password=body.password, device_id=device_id)
    except ImportError as e:
        raise HTTPException(
            status_code=400,
            detail=(
                "当前后端未安装 SOCKS 代理支持依赖，请安装 socksio 或改用 http:// 代理地址。"
                f" 原始错误: {str(e)[:300]}"
            ),
        ) from e

    try:
        login_data = await driver.login()
        quota = await driver.get_quota()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            data = e.response.json()
            if isinstance(data, dict):
                detail = str(
                    data.get("message")
                    or data.get("error_description")
                    or data.get("error")
                    or data
                )
            else:
                detail = str(data)
        except Exception:
            detail = (e.response.text or "")[:500]
        if e.response.status_code == 400 and not detail:
            detail = "账号密码错误或触发 PikPak 验证策略（可能需要验证码 token）"
        raise HTTPException(
            status_code=400,
            detail=(
                f"PikPak 登录验证失败({e.response.status_code})：{detail}。"
                "如果在大陆网络环境，请先配置 PIKPAK_PROXY。"
            ),
        ) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"PikPak 登录验证失败: {str(e)[:500]}") from e
    finally:
        await driver.aclose()

    daily = settings.FREE_DAILY_TASK_LIMIT if body.pool_type == "FREE" else -1
    acc = PikPakAccount(
        email=body.email,
        password_enc=encrypt_secret(body.password),
        refresh_token=login_data.get("refresh_token"),
        device_id=device_id,
        pool_type=body.pool_type,
        quota_total=quota.get("limit", 6_442_450_944),
        quota_used=quota.get("usage", 0),
        daily_tasks_left=daily,
        status="ACTIVE",
        last_synced_at=datetime.now(timezone.utc),
    )

    db.add(acc)
    await db.commit()
    await db.refresh(acc)

    return {
        "id": str(acc.id),
        "email": acc.email,
        "pool_type": acc.pool_type,
        "status": acc.status,
        "message": "账号验证成功，已加入号池",
    }


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_account(
    account_id: uuid.UUID,
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> None:
    acc = await db.get(PikPakAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    await db.delete(acc)
    await db.commit()


@router.post(
    "/accounts/{account_id}/cleanup-drive",
    response_model=PikPakAccountCleanupDriveResponse,
)
async def cleanup_account_drive(
    account_id: uuid.UUID,
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> PikPakAccountCleanupDriveResponse:
    """
    风险操作：清空该账号网盘根目录下 **PPHUB** 文件夹内的全部内容并清空回收站。
    用户存放在其它目录的文件不受影响；离线任务均写入 PPHUB。
    """
    from datetime import datetime, timezone

    acc = await db.get(PikPakAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    pwd = decrypt_secret(acc.password_enc)
    driver = PikPakDriver(email=acc.email, password=pwd, device_id=acc.device_id, refresh_token=acc.refresh_token)
    try:
        await driver.ensure_token()
        hub_id = acc.hub_folder_id or await driver.ensure_pphub_folder_id()
        acc.hub_folder_id = hub_id

        scanned_ids = await driver.list_descendants_under_folder_for_cleanup(hub_id, with_audit=False)
        scanned_count = len(scanned_ids)
        original_target_ids = list(dict.fromkeys(scanned_ids))
        target_file_ids = list(original_target_ids)

        delete_error_snips: list[str] = []
        emptied_ok = True
        max_trash_rounds = 10

        if target_file_ids:
            working = list(target_file_ids)
            chunk_size = 200
            for rnd in range(max_trash_rounds):
                if not working:
                    break
                for i in range(0, len(working), chunk_size):
                    chunk = working[i : i + chunk_size]
                    ok, http_code, body_snip = await driver.delete_files_verbose(chunk)
                    if not ok:
                        delete_error_snips.append(
                            f"trash_r{rnd} http={http_code} ids={len(chunk)} body={body_snip or '<empty>'}"
                        )
                emptied_ok = bool(await driver.empty_trash()) and emptied_ok
                latest_ids = set(await driver.list_all_file_ids(for_trash_cleanup=True))
                working = [x for x in working if x in latest_ids]
                if not working:
                    break

            if working:
                for i in range(0, len(working), chunk_size):
                    chunk = working[i : i + chunk_size]
                    ok, http_code, body_snip = await driver.delete_files_permanent_verbose(chunk)
                    if not ok:
                        delete_error_snips.append(
                            f"perm http={http_code} ids={len(chunk)} body={body_snip or '<empty>'}"
                        )
                emptied_ok = bool(await driver.empty_trash()) and emptied_ok

        remaining_count = 0
        deleted_ok = True
        deleted_count = 0
        failed_count = 0
        if original_target_ids:
            latest_ids = set(await driver.list_all_file_ids(for_trash_cleanup=True))
            remaining_count = len(set(original_target_ids).intersection(latest_ids))
            deleted_ok = remaining_count == 0
            deleted_count = max(len(original_target_ids) - remaining_count, 0)
            failed_count = remaining_count

        now = datetime.now(timezone.utc)
        cleanup_ok = bool(deleted_ok and emptied_ok)
        if cleanup_ok and original_target_ids:
            # 仅在删除与清空回收站都成功时，才标记任务失效并解绑 file_id。
            task_ids_result = await db.execute(
                select(Task.id)
                .where(Task.pikpak_account_id == acc.id)
                .where(Task.file_id.in_(original_target_ids))
            )
            stale_task_ids = list(task_ids_result.scalars().all())
            await db.execute(
                Task.__table__.update()
                .where(Task.__table__.c.pikpak_account_id == acc.id)
                .where(Task.__table__.c.file_id.in_(original_target_ids))
                .values(
                    status="EXPIRED",
                    expire_at=now,
                    completed_at=None,
                    progress=0,
                    file_id=None,
                    pikpak_task_id=None,
                    error_message="管理员清空网盘已删除源文件",
                )
            )
            await clear_task_links(db, stale_task_ids)

        # refresh_token 可能因重新登录而更新
        acc.refresh_token = driver.refresh_token or acc.refresh_token
        try:
            quota = await driver.get_quota()
            acc.quota_total = int(quota["limit"])
            acc.quota_used = int(quota["usage"])
            acc.last_synced_at = now
        except Exception:  # noqa: BLE001
            # 用量同步失败时保留原值，避免“显示归零但实际未删”。
            pass
        await db.commit()

        status_suffix = "成功" if cleanup_ok else "未完全成功"
        return PikPakAccountCleanupDriveResponse(
            ok=cleanup_ok,
            message=(
                f"清空网盘{status_suffix}：删除成功 {deleted_count}"
                + (f"，删除失败 {failed_count}" if failed_count else "")
                + (f"，仍存在 {remaining_count}" if remaining_count else "")
                + f"，回收站清空 {emptied_ok}"
                + f"（PPHUB 内条目 {scanned_count}）"
                + (f"；删除失败详情：{' | '.join(delete_error_snips[:2])}" if delete_error_snips else "")
            ),
            deleted_files=deleted_count,
        )
    finally:
        await driver.aclose()


@router.post(
    "/accounts/{account_id}/sync-from-pikpak",
    response_model=PikPakAccountSyncFromApiResponse,
)
async def sync_account_from_pikpak(
    account_id: uuid.UUID,
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> PikPakAccountSyncFromApiResponse:
    """
    在线拉取 PikPak `drive/v1/about`：同步网盘用量与 refresh_token。
    「剩次数」优先仍为本 Hub 计数；若 about 响应中出现可识别的离线剩余字段则写回（见实现内键名列表）。
    """
    acc = await db.get(PikPakAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    pwd = decrypt_secret(acc.password_enc)
    driver = PikPakDriver(
        email=acc.email,
        password=pwd,
        device_id=acc.device_id,
        refresh_token=acc.refresh_token,
    )
    try:
        await driver.ensure_token()
        about = await driver.get_drive_about()
        acc.hub_folder_id = await driver.ensure_pphub_folder_id()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            data = e.response.json()
            if isinstance(data, dict):
                detail = str(
                    data.get("message")
                    or data.get("error_description")
                    or data.get("error")
                    or data
                )
            else:
                detail = str(data)
        except Exception:
            detail = (e.response.text or "")[:500]
        raise HTTPException(
            status_code=400,
            detail=f"PikPak about 请求失败（{e.response.status_code}）：{detail or '未知错误'}",
        ) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"PikPak 同步失败: {str(e)[:500]}") from e
    finally:
        await driver.aclose()

    quota = about.get("quota") if isinstance(about.get("quota"), dict) else {}
    limit = _int_from_quota_json(quota.get("limit"))
    usage = _int_from_quota_json(quota.get("usage"))
    if limit <= 0:
        limit = max(int(acc.quota_total or 0), 1)
    acc.quota_total = limit
    acc.quota_used = min(usage, limit)
    acc.last_synced_at = datetime.now(timezone.utc)
    acc.refresh_token = driver.refresh_token

    parsed = _parse_offline_remaining_from_about(about)
    updated_daily = False
    if acc.pool_type == "FREE":
        if parsed is not None:
            acc.daily_tasks_left = parsed
            updated_daily = True

    await db.commit()

    _admin_log.warning(
        "pikpak_about_sync account_id=%s email=%s pool=%s about_top_keys=%s parsed_offline_remaining=%s daily_tasks_left_applied=%s",
        account_id,
        acc.email,
        acc.pool_type,
        list(about.keys()),
        parsed,
        int(acc.daily_tasks_left) if acc.pool_type == "FREE" else None,
    )

    used_mb = acc.quota_used // (1024 * 1024)
    total_mb = acc.quota_total // (1024 * 1024)
    msg_parts = [f"已从 PikPak 同步网盘用量：约 {used_mb} MB / {total_mb} MB"]
    if acc.pool_type == "FREE":
        if parsed is not None:
            msg_parts.append(f"剩次数已按 PikPak about 中解析到的剩余次数更新为：{acc.daily_tasks_left}")
        else:
            msg_parts.append("about 未返回可识别的离线剩余字段：剩次数未知（以 PikPak 为准），Hub 将保留原值不覆盖")
    else:
        msg_parts.append("付费池离线次数为不限制（Hub 记为 -1）")

    return PikPakAccountSyncFromApiResponse(
        ok=True,
        message="；".join(msg_parts),
        quota_total=int(acc.quota_total),
        quota_used=int(acc.quota_used),
        daily_tasks_left=int(acc.daily_tasks_left),
        offline_remaining_from_pikpak=parsed,
        updated_daily_tasks_from_pikpak=updated_daily,
    )


@router.get("/tasks", response_model=TaskAdminListResponse)
async def admin_tasks(
    db=Depends(get_session),
    _: User = Depends(require_admin),
    status: Optional[str] = Query(default=None, description="PENDING/SUBMITTED/DOWNLOADING/COMPLETED/FAILED/EXPIRED 或 ALL"),
    pool_type: Optional[str] = Query(default=None, description="FREE/PREMIUM 或 ALL"),
    email: Optional[str] = Query(default=None, description="按用户邮箱模糊匹配"),
    order_by: str = Query(default="created_at_desc"),
) -> TaskAdminListResponse:
    allowed_status = {"PENDING", "SUBMITTED", "DOWNLOADING", "COMPLETED", "FAILED", "EXPIRED"}
    allowed_order = {"created_at_desc", "created_at_asc"}
    allowed_pool = {"FREE", "PREMIUM"}
    st = status.strip().upper() if status and status.strip() else None
    pt = pool_type.strip().upper() if pool_type and pool_type.strip() else None

    if order_by not in allowed_order:
        raise HTTPException(status_code=400, detail="order_by 仅支持 created_at_desc / created_at_asc")
    if st and st != "ALL" and st not in allowed_status:
        raise HTTPException(status_code=400, detail="status 不在允许范围内")
    if pt and pt != "ALL" and pt not in allowed_pool:
        raise HTTPException(status_code=400, detail="pool_type 仅支持 FREE / PREMIUM")

    stmt = (
        select(
            Task.id,
            Task.file_name,
            Task.file_size,
            Task.status,
            Task.progress,
            Task.created_at,
            Task.completed_at,
            User.email.label("user_email"),
            PikPakAccount.email.label("account_email"),
            PikPakAccount.pool_type.label("pool_type"),
        )
        .select_from(Task)
        .join(User, User.id == Task.user_id)
        .outerjoin(PikPakAccount, PikPakAccount.id == Task.pikpak_account_id)
    )

    if st and st != "ALL":
        stmt = stmt.where(Task.status == st)
    if pt and pt != "ALL":
        stmt = stmt.where(PikPakAccount.pool_type == pt)
    if email and email.strip():
        stmt = stmt.where(User.email.ilike(f"%{email.strip()}%"))

    if order_by == "created_at_desc":
        stmt = stmt.order_by(Task.created_at.desc())
    else:
        stmt = stmt.order_by(Task.created_at.asc())

    result = await db.execute(stmt)
    rows = result.all()
    tasks: list[TaskAdminListItem] = []
    for r in rows:
        (
            job_id,
            file_name,
            file_size,
            tstatus,
            progress,
            created_at,
            completed_at,
            user_email,
            account_email,
            pool_t,
        ) = r
        tasks.append(
            TaskAdminListItem(
                job_id=str(job_id),
                user_email=user_email,
                account_email=account_email,
                pool_type=pool_t,
                file_name=file_name,
                file_size=int(file_size or 0),
                status=str(tstatus),
                progress=int(progress or 0),
                created_at=created_at.isoformat() if created_at else None,
                completed_at=completed_at.isoformat() if completed_at else None,
            )
        )

    return TaskAdminListResponse(tasks=tasks, total=len(tasks))


@router.patch("/users/{user_id}", response_model=UserPublic)
async def patch_user_level(
    user_id: uuid.UUID,
    body: UserAdminPatch,
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> UserPublic:
    if body.level not in _LIMITED_LEVELS:
        raise HTTPException(status_code=400, detail="无效的等级")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.level = body.level
    await db.commit()
    await db.refresh(user)
    return UserPublic.model_validate(user)


@router.post("/users/batch-delete", response_model=dict)
async def batch_delete_users(
    body: UserAdminBatchDeleteRequest,
    db=Depends(get_session),
    admin: User = Depends(require_admin),
) -> dict:
    ids = {uid for uid in body.user_ids if uid != admin.id}
    if not ids:
        return {"deleted": 0, "message": "没有可删除的用户"}

    result = await db.execute(select(User).where(User.id.in_(ids)))
    users = list(result.scalars().all())
    for user in users:
        await db.delete(user)
    await db.commit()
    return {"deleted": len(users), "message": f"已删除 {len(users)} 个用户"}


@router.post("/users/{user_id}/reset-password", response_model=dict)
async def reset_user_password(
    user_id: uuid.UUID,
    body: UserAdminResetPasswordRequest,
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> dict:
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"message": "密码已重置"}


@router.get("/stats", response_model=dict)
async def admin_stats(
    db=Depends(get_session),
    _: User = Depends(require_admin),
) -> dict:
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    vip_users = (await db.execute(select(func.count()).select_from(User).where(User.level == "VIP"))).scalar_one()

    free_pool = await db.execute(
        select(func.count()).select_from(PikPakAccount).where(PikPakAccount.pool_type == "FREE"),
    )
    free_active = await db.execute(
        select(func.count())
        .select_from(PikPakAccount)
        .where(PikPakAccount.pool_type == "FREE", PikPakAccount.status == "ACTIVE"),
    )
    prem_pool = await db.execute(
        select(func.count()).select_from(PikPakAccount).where(PikPakAccount.pool_type == "PREMIUM"),
    )
    prem_active = await db.execute(
        select(func.count())
        .select_from(PikPakAccount)
        .where(PikPakAccount.pool_type == "PREMIUM", PikPakAccount.status == "ACTIVE"),
    )

    async def pool_avg_pct(pool_type: str) -> float:
        r = await db.execute(select(PikPakAccount).where(PikPakAccount.pool_type == pool_type))
        accs = list(r.scalars().all())
        if not accs:
            return 0.0
        pcts: list[float] = []
        for a in accs:
            total = max(int(a.quota_total or 1), 1)
            used = min(int(a.quota_used or 0), total)
            pcts.append(used * 100.0 / total)
        return round(sum(pcts) / len(pcts), 2)

    start_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    tasks_today = (
        await db.execute(select(func.count()).select_from(Task).where(Task.created_at >= start_today))
    ).scalar_one()
    tasks_completed = (
        await db.execute(select(func.count()).select_from(Task).where(Task.status == "COMPLETED"))
    ).scalar_one()
    tasks_failed = (await db.execute(select(func.count()).select_from(Task).where(Task.status == "FAILED"))).scalar_one()

    return {
        "total_users": int(total_users or 0),
        "vip_users": int(vip_users or 0),
        "free_pool": {
            "count": int(free_pool.scalar_one() or 0),
            "active": int(free_active.scalar_one() or 0),
            "avg_usage_percent": await pool_avg_pct("FREE"),
        },
        "premium_pool": {
            "count": int(prem_pool.scalar_one() or 0),
            "active": int(prem_active.scalar_one() or 0),
            "avg_usage_percent": await pool_avg_pct("PREMIUM"),
        },
        "tasks_today": int(tasks_today or 0),
        "tasks_completed": int(tasks_completed or 0),
        "tasks_failed": int(tasks_failed or 0),
    }
