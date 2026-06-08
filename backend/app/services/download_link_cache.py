from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.download_link_cache import DownloadLinkCache


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_expire_at() -> datetime:
    ttl_h = max(1, int(settings.DOWNLOAD_LINK_CACHE_TTL_HOURS))
    return _utcnow() + timedelta(hours=ttl_h)


async def get_valid_task_links(db: AsyncSession, task_id: uuid.UUID) -> list[DownloadLinkCache]:
    now = _utcnow()
    result = await db.execute(
        select(DownloadLinkCache)
        .where(DownloadLinkCache.task_id == task_id)
        .where(DownloadLinkCache.expires_at > now)
        .order_by(DownloadLinkCache.sort_index.asc(), DownloadLinkCache.id.asc())
    )
    return list(result.scalars().all())


async def replace_task_links(db: AsyncSession, task_id: uuid.UUID, links: list[dict[str, Any]]) -> None:
    await db.execute(delete(DownloadLinkCache).where(DownloadLinkCache.task_id == task_id))
    expire_at = _ttl_expire_at()
    for idx, item in enumerate(links):
        url = str(item.get("url") or "").strip()
        file_id = str(item.get("file_id") or "").strip()
        if not url or not file_id:
            continue
        episode = item.get("episode") if isinstance(item.get("episode"), dict) else {}
        season = episode.get("season")
        number = episode.get("episode")
        db.add(
            DownloadLinkCache(
                task_id=task_id,
                file_id=file_id,
                name=str(item.get("name") or ""),
                size=int(item.get("size") or 0),
                episode_key=str(item.get("episode_key") or "") or None,
                episode_season=int(season) if isinstance(season, int) else None,
                episode_number=int(number) if isinstance(number, int) else None,
                url=url,
                is_primary=bool(item.get("is_primary")),
                sort_index=int(item.get("sort_index") if item.get("sort_index") is not None else idx),
                expires_at=expire_at,
            )
        )


async def clear_task_links(db: AsyncSession, task_ids: list[uuid.UUID]) -> None:
    if not task_ids:
        return
    await db.execute(delete(DownloadLinkCache).where(DownloadLinkCache.task_id.in_(task_ids)))
