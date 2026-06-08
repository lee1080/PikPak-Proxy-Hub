from __future__ import annotations

import json
from typing import Any, Optional

import httpx

# PikPak 免费号池空间不足时的标准错误体（与官方 API 一致，便于前端/运维识别）
PREMIUM_REQUIRED_ERROR: dict[str, Any] = {
    "error": "file_space_not_enough",
    "error_code": 8,
    "error_url": "",
    "error_description": "Insufficient cloud storage, continued use requires Premium",
    "error_details": [],
}

PREMIUM_REQUIRED_ERROR_JSON = json.dumps(PREMIUM_REQUIRED_ERROR, ensure_ascii=False)


def premium_required_message() -> str:
    return PREMIUM_REQUIRED_ERROR_JSON


def task_exceeds_pool_quota(required_bytes: int, quota_total: int) -> bool:
    """单任务体积超过号池网盘总容量时，清理历史任务也无法容纳。"""
    if required_bytes <= 0 or quota_total <= 0:
        return False
    return required_bytes > quota_total


def account_free_bytes(quota_total: int, quota_used: int) -> int:
    return max(0, int(quota_total) - int(quota_used))


def _body_dict(exc: httpx.HTTPStatusError) -> Optional[dict[str, Any]]:
    try:
        data = exc.response.json()
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        pass
    return None


def looks_like_insufficient_space_http(exc: httpx.HTTPStatusError) -> bool:
    """
    PikPak 空间不足：可能是 507，也可能是 JSON 体 file_space_not_enough / error_code=8。
    """
    try:
        code = int(exc.response.status_code)
    except Exception:  # noqa: BLE001
        code = 0
    if code == 507:
        return True

    body_obj = _body_dict(exc)
    if body_obj:
        if str(body_obj.get("error") or "").lower() == "file_space_not_enough":
            return True
        err_code = body_obj.get("error_code")
        if err_code == 8 or str(err_code) == "8":
            return True
        desc = str(body_obj.get("error_description") or "").lower()
        if "insufficient cloud storage" in desc or "requires premium" in desc:
            return True

    body = ""
    try:
        body = (exc.response.text or "")[:900].lower()
    except Exception:  # noqa: BLE001
        body = ""
    if not body:
        return False
    if any(x in body for x in ("invalid_grant", "captcha", "unauthorized", "forbidden")):
        return False
    return any(
        x in body
        for x in (
            "file_space_not_enough",
            "insufficient cloud storage",
            "requires premium",
            "insufficient",
            "not enough",
            "no space",
            "insufficient storage",
            "storage full",
            "quota exceeded",
            "quota",
        )
    )


def response_body_snippet(exc: httpx.HTTPStatusError, limit: int = 1000) -> str:
    body_obj = _body_dict(exc)
    if body_obj:
        return json.dumps(body_obj, ensure_ascii=False)[:limit]
    try:
        return (exc.response.text or str(exc.response.status_code))[:limit]
    except Exception:  # noqa: BLE001
        return str(exc.response.status_code)
