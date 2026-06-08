import re
import hashlib
import urllib.parse
from enum import Enum
from typing import Optional

import bencodepy
import httpx

LINK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("MAGNET", re.compile(r"^magnet:\?xt=urn:[a-z0-9]+:[a-zA-Z0-9]{32,}", re.I)),
    ("ED2K", re.compile(r"^ed2k://\|file\|", re.I)),
    ("TORRENT", re.compile(r"\.torrent(\?.*)?$", re.I)),
    ("SOCIAL", re.compile(
        r"(twitter\.com|x\.com|tiktok\.com|t\.co|facebook\.com|instagram\.com|t\.me|telegram\.me)",
        re.I,
    )),
    ("HTTP", re.compile(r"^https?://", re.I)),
]


class LinkType(str, Enum):
    MAGNET = "MAGNET"
    TORRENT = "TORRENT"
    HTTP = "HTTP"
    SOCIAL = "SOCIAL"
    ED2K = "ED2K"
    UNKNOWN = "UNKNOWN"


def detect_link_type(url: str) -> LinkType:
    for code, pattern in LINK_PATTERNS:
        if pattern.search(url.strip()):
            return LinkType(code)  # type: ignore[arg-type]
    return LinkType.UNKNOWN


async def estimate_file_size(url: str, link_type: LinkType) -> Optional[int]:
    u = (url or "").strip()
    if link_type == LinkType.MAGNET:
        # 常见磁力带 xl=（exact length），可用于容量预检与清理策略
        try:
            parsed = urllib.parse.urlparse(u)
            qs = urllib.parse.parse_qs(parsed.query)
            xl = qs.get("xl", [None])[0]
            if xl is None:
                return None
            val = int(str(xl).strip())
            return val if val > 0 else None
        except Exception:
            return None
    if link_type == LinkType.ED2K:
        # ed2k://|file|name|size|hash|/
        try:
            parts = u.split("|")
            if len(parts) >= 5:
                val = int(parts[3])
                return val if val > 0 else None
        except Exception:
            return None
        return None
    if link_type != LinkType.HTTP:
        return None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.head(u)
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:
        return None


def _iter_torrent_files(info: dict) -> int:
    if b"length" in info:
        return int(info.get(b"length", 0))
    files = info.get(b"files", [])
    total = 0
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict):
                total += int(item.get(b"length", 0))
    return total


def parse_torrent_file(path: str) -> dict:
    raw = open(path, "rb").read()
    data = bencodepy.decode(raw)
    info = data.get(b"info")
    if not isinstance(info, dict):
        raise ValueError("无效的 torrent 文件：缺少 info 字段")

    info_hash = hashlib.sha1(bencodepy.encode(info)).hexdigest()
    name_bytes = info.get(b"name", b"")
    name = name_bytes.decode("utf-8", errors="ignore") if isinstance(name_bytes, bytes) else ""

    trackers: list[str] = []
    announce = data.get(b"announce")
    if isinstance(announce, bytes):
        trackers.append(announce.decode("utf-8", errors="ignore"))
    announce_list = data.get(b"announce-list")
    if isinstance(announce_list, list):
        for tier in announce_list:
            if isinstance(tier, list):
                for tr in tier:
                    if isinstance(tr, bytes):
                        trackers.append(tr.decode("utf-8", errors="ignore"))

    # 保序去重
    dedup_trackers = list(dict.fromkeys([t for t in trackers if t]))

    params = [("xt", f"urn:btih:{info_hash}")]
    if name:
        params.append(("dn", name))
    for tr in dedup_trackers:
        params.append(("tr", tr))
    magnet = "magnet:?" + urllib.parse.urlencode(params, doseq=True)

    return {
        "name": name or None,
        "size": _iter_torrent_files(info),
        "info_hash": info_hash,
        "magnet": magnet,
    }
