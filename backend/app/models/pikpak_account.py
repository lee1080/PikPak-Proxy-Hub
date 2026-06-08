from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PikPakAccount(Base):
    __tablename__ = "pikpak_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pool_type: Mapped[str] = mapped_column(String(16), nullable=False, default="FREE")
    quota_total: Mapped[int] = mapped_column(BigInteger, default=6_442_450_944)
    quota_used: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_tasks_left: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    # PikPak 网盘根目录下名为 PPHUB 的专用文件夹 id（离线任务写入此目录，清空网盘仅清理此目录）
    hub_folder_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
