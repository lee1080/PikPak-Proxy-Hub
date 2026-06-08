"""创建管理员账号: python -m app.scripts.create_admin --username admin --password xxx"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal, Base, engine
from app.models.user import User
from app.utils.security import hash_password


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--email", default=None)
    args = parser.parse_args()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(User).where(User.username == args.username))
        if existing.scalar_one_or_none():
            print("用户名已存在")
            return

        email = args.email
        if email:
            e = await db.execute(select(User).where(User.email == email))
            if e.scalar_one_or_none():
                print("邮箱已被使用")
                return

        user = User(
            username=args.username,
            email=email,
            password_hash=hash_password(args.password),
            level="ADMIN",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"管理员创建成功 id={user.id}")


if __name__ == "__main__":
    asyncio.run(main())
