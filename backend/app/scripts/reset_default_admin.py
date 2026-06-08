"""重置默认管理员账号密码（忘记密码时在终端执行）

重置为：
--username admin --password admin123

并强制下次登录修改密码（must_change_password=True）
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.utils.security import hash_password


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"


async def reset() -> None:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.username == DEFAULT_USERNAME))
        user = r.scalar_one_or_none()
        if not user:
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
            return
        user.username = DEFAULT_USERNAME
        user.password_hash = hash_password(DEFAULT_PASSWORD)
        user.level = "ADMIN"
        user.is_active = True
        user.must_change_password = True
        await db.commit()
        await db.refresh(user)
        print(f"default admin reset id={user.id}")


def main() -> None:
    asyncio.run(reset())


if __name__ == "__main__":
    main()

