from __future__ import annotations

import time
from typing import Optional

from app.services.pikpak_url_utils import pikpak_url_expire_epoch

_CACHE: dict[str, tuple[str, float]] = {}


def _cache_key(account_id: str, file_id: str) -> str:
    return f"{account_id}:{file_id}"


def get_cached_file_link(account_id: str, file_id: str) -> Optional[str]:
    key = _cache_key(account_id, file_id)
    hit = _CACHE.get(key)
    if not hit:
        return None
    url, expires_mono = hit
    if time.monotonic() >= expires_mono:
        _CACHE.pop(key, None)
        return None
    return url


def set_cached_file_link(account_id: str, file_id: str, url: str) -> None:
    u = (url or "").strip()
    if not u:
        return
    exp_epoch = pikpak_url_expire_epoch(u)
    if exp_epoch is not None:
        ttl = max(60.0, exp_epoch - time.time() - 120.0)
    else:
        ttl = 30 * 60.0
    key = _cache_key(account_id, file_id)
    _CACHE[key] = (u, time.monotonic() + ttl)
