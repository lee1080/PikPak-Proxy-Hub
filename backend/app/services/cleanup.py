import uuid
from typing import Optional

from sqlalchemy import asc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pikpak_account import PikPakAccount
from app.models.task import Task
from app.services.download_link_cache import clear_task_links
from app.services.pikpak_driver import PikPakDriver
from app.services.space_policy import account_free_bytes


class CleanupService:
    def __init__(self, db: AsyncSession, driver: PikPakDriver):
        self.db = db
        self.driver = driver

    def _free_bytes(self, account: PikPakAccount) -> int:
        return account_free_bytes(int(account.quota_total), int(account.quota_used))

    async def _list_completed_tasks_fifo(
        self,
        account: PikPakAccount,
        *,
        exclude_task_id: Optional[uuid.UUID] = None,
    ) -> list[Task]:
        """按最早完成（无完成时间则按创建时间）排序的已完成任务。"""
        stmt = (
            select(Task)
            .where(Task.pikpak_account_id == account.id)
            .where(Task.status == "COMPLETED")
            .where(Task.file_id.isnot(None))
            .order_by(asc(func.coalesce(Task.completed_at, Task.created_at)))
        )
        if exclude_task_id is not None:
            stmt = stmt.where(Task.id != exclude_task_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def delete_oldest_completed_batch(
        self,
        account: PikPakAccount,
        *,
        exclude_task_id: Optional[uuid.UUID] = None,
        stop_when_free_gte: Optional[int] = None,
    ) -> bool:
        """
        FIFO 删除最早完成的 Hub 任务文件，至少删一批则返回 True。
        stop_when_free_gte：删除到剩余空间 >= 该值时停止（用于为新任务腾位）。
        """
        old_tasks = await self._list_completed_tasks_fifo(account, exclude_task_id=exclude_task_id)
        if not old_tasks:
            return False

        free_space = self._free_bytes(account)
        if stop_when_free_gte is not None and free_space >= int(stop_when_free_gte):
            return True

        stale_task_ids: list = []
        freed = 0
        to_delete: list[str] = []
        for task in old_tasks:
            if not task.file_id:
                continue
            stale_task_ids.append(task.id)
            to_delete.append(task.file_id)
            size = int(task.file_size or 0)
            if size <= 0:
                meta = await self.driver.get_file_dict(str(task.file_id))
                try:
                    size = int(meta.get("size") or 0)
                except Exception:
                    size = 0
            freed += max(0, size)
            need = int(stop_when_free_gte or 0)
            if need > 0 and (free_space + freed) >= need:
                break

        if not to_delete:
            return False

        await self._expire_and_delete_files(account, stale_task_ids, to_delete)
        return True

    async def _expire_and_delete_files(
        self,
        account: PikPakAccount,
        stale_task_ids: list,
        to_delete: list[str],
    ) -> int:
        to_delete_unique = list(dict.fromkeys(to_delete))
        await self.db.execute(
            update(Task)
            .where(Task.pikpak_account_id == account.id)
            .where(Task.file_id.in_(to_delete_unique))
            .values(
                status="EXPIRED",
                file_id=None,
                pikpak_task_id=None,
                error_message="网盘文件因空间清理已删除",
            )
        )
        await clear_task_links(self.db, stale_task_ids)

        await self.driver.delete_files(to_delete_unique)
        await self.driver.empty_trash()

        try:
            quota = await self.driver.get_quota()
            account.quota_total = int(quota["limit"])
            account.quota_used = int(quota["usage"])
        except Exception:
            pass
        await self.db.commit()
        return len(to_delete_unique)

    async def ensure_space(
        self,
        account: PikPakAccount,
        required_bytes: int,
        *,
        exclude_task_id: Optional[uuid.UUID] = None,
    ) -> bool:
        free_space = self._free_bytes(account)
        if required_bytes <= 0 or free_space >= required_bytes:
            return True

        result = await self.db.execute(
            select(Task)
            .where(Task.pikpak_account_id == account.id)
            .where(Task.status == "COMPLETED")
            .where(Task.file_id.isnot(None))
            .order_by(asc(func.coalesce(Task.completed_at, Task.created_at)))
        )
        old_tasks = list(result.scalars().all())
        stale_task_ids: list = []

        freed = 0
        to_delete: list[str] = []
        for task in old_tasks:
            if not task.file_id:
                continue
            stale_task_ids.append(task.id)
            to_delete.append(task.file_id)
            size = int(task.file_size or 0)
            if size <= 0:
                # DB 里可能缺 file_size：尽量向 PikPak 取一次元信息，避免 freed 过小导致删不够
                meta = await self.driver.get_file_dict(str(task.file_id))
                try:
                    size = int(meta.get("size") or 0)
                except Exception:
                    size = 0
            freed += max(0, size)
            if (free_space + freed) >= required_bytes:
                break

        if not to_delete:
            return False

        await self._expire_and_delete_files(account, stale_task_ids, to_delete)
        free2 = self._free_bytes(account)
        return free2 >= required_bytes

    async def purge_all_completed_fifo(
        self,
        account: PikPakAccount,
        *,
        exclude_task_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """无法估算新任务体积时：按 FIFO 清空所有可清理的已完成任务。"""
        deleted_any = False
        while True:
            old_tasks = await self._list_completed_tasks_fifo(account, exclude_task_id=exclude_task_id)
            if not old_tasks:
                break
            stale_task_ids: list = []
            to_delete: list[str] = []
            for task in old_tasks:
                if not task.file_id:
                    continue
                stale_task_ids.append(task.id)
                to_delete.append(task.file_id)
            if not to_delete:
                break
            await self._expire_and_delete_files(account, stale_task_ids, to_delete)
            deleted_any = True
        return deleted_any
