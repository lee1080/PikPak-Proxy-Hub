"""Stable content keys for cross-user offline deduplication."""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qsl, urlparse, urlencode

from app.services.link_parser import LinkType

_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[a-z2-7]{32})", re.I)


def magnet_content_key(url: str) -> Optional[str]:
    u = url.strip()
    m = _BTIH_RE.search(u)
    if not m:
        q = urlparse(u).query
        for _, v in parse_qsl(q, keep_blank_values=True):
            m = _BTIH_RE.search(v)
            if m:
                break
    if not m:
        return None
    return f"magnet:{m.group(1).lower()}"


def _normalize_http_url(url: str) -> str:
    p = urlparse(url.strip())
    scheme = (p.scheme or "http").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or ""
    q = parse_qsl(p.query, keep_blank_values=True)
    q.sort()
    qs = urlencode(q)
    return f"{scheme}://{netloc}{path}" + (f"?{qs}" if qs else "")


def http_content_key(url: str) -> str:
    norm = _normalize_http_url(url)
    return "url:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


def content_key_for_url(url: str, link_type: LinkType) -> Optional[str]:
    u = url.strip()
    if link_type in (LinkType.MAGNET, LinkType.TORRENT):
        return magnet_content_key(u)
    if link_type == LinkType.HTTP:
        return http_content_key(u)
    if link_type == LinkType.ED2K:
        return "ed2k:" + hashlib.sha256(u.encode("utf-8")).hexdigest()
    if link_type == LinkType.SOCIAL:
        return "social:" + hashlib.sha256(u.encode("utf-8")).hexdigest()
    return None
