"""确保默认管理员存在（部署时调用）

默认账号密码：
--username admin --password admin123

首次登录后强制修改密码：must_change_password=True
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.utils.security import hash_password


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"


async def ensure() -> None:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.username == DEFAULT_USERNAME))
        user = r.scalar_one_or_none()
        if user:
            return
        user = User(
            username=DEFAULT_USERNAME,
            email=None,
            password_hash=hash_password(DEFAULT_PASSWORD),
            level="ADMIN",
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"default admin created id={user.id}")


def main() -> None:
    asyncio.run(ensure())


if __name__ == "__main__":
    main()

