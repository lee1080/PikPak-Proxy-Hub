from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pikpak_account import PikPakAccount

_rr_pointer: dict[str, int] = {"FREE": 0, "PREMIUM": 0}


async def pick_account(
    db: AsyncSession,
    pool_type: str,
    required_size: int,
) -> Optional[PikPakAccount]:
    result = await db.execute(
        select(PikPakAccount)
        .where(PikPakAccount.pool_type == pool_type)
        .where(PikPakAccount.status == "ACTIVE")
        .where(or_(PikPakAccount.daily_tasks_left > 0, PikPakAccount.daily_tasks_left == -1))
        .order_by(PikPakAccount.id)
    )
    pool = list(result.scalars().all())
    if not pool:
        return None

    idx = _rr_pointer.get(pool_type, 0)
    n = len(pool)
    for i in range(n):
        candidate = pool[(idx + i) % n]
        free_space = int(candidate.quota_total) - int(candidate.quota_used)
        if free_space >= required_size:
            _rr_pointer[pool_type] = (idx + i + 1) % n
            return candidate
    return None


def pool_type_for_user_level(level: str) -> str:
    if level in ("VIP", "ADMIN"):
        return "PREMIUM"
    return "FREE"


async def pick_account_for_user(
    db: AsyncSession,
    user_level: str,
    required_size: int,
) -> Optional[PikPakAccount]:
    """
    按用户等级选号池账号。VIP/ADMIN 优先 PREMIUM；
    若 PREMIUM 池无可用账号（常见于仅配置了 FREE 号池的自建环境），回退到 FREE 池。
    """
    pool = pool_type_for_user_level(user_level)
    account = await pick_account(db, pool, required_size)
    if account is not None:
        return account
    if pool == "PREMIUM":
        return await pick_account(db, "FREE", required_size)
    return None
