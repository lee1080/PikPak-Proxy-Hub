from __future__ import annotations

import time
from typing import Optional
from urllib.parse import parse_qs, urlparse


def pikpak_url_expire_epoch(url: str) -> Optional[int]:
    try:
        qs = parse_qs(urlparse(url).query)
        raw = (qs.get("expire") or [None])[0]
        if raw is None:
            return None
        sec = int(str(raw))
        return sec if sec > 0 else None
    except (TypeError, ValueError):
        return None


def pikpak_url_is_expired(url: str, *, skew_sec: int = 120) -> bool:
    exp = pikpak_url_expire_epoch(url)
    if exp is None:
        return False
    return time.time() >= exp - max(0, skew_sec)
