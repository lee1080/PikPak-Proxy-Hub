from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hub_meta import HubMeta

RR_POINTER_PREFIX = "rr_pointer:"


async def get_rr_pointer(db: AsyncSession, pool_type: str) -> int:
    key = f"{RR_POINTER_PREFIX}{pool_type}"
    row = await db.get(HubMeta, key)
    if row is None:
        return 0
    try:
        return max(0, int(row.value))
    except (TypeError, ValueError):
        return 0


async def set_rr_pointer(db: AsyncSession, pool_type: str, idx: int) -> None:
    key = f"{RR_POINTER_PREFIX}{pool_type}"
    val = str(max(0, int(idx)))
    row = await db.get(HubMeta, key)
    if row is None:
        db.add(HubMeta(key=key, value=val))
    else:
        row.value = val
