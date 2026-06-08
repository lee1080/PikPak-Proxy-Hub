"""内存环形日志缓冲，供管理后台 /admin/logs 查看近期运行日志。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings
from app.services.admin_log_db import try_persist_admin_runtime_log

_buffer: Optional["AdminLogBuffer"] = None
_handler_installed = False


class AdminLogBuffer:
    def __init__(self, maxlen: int) -> None:
        self._lock = threading.Lock()
        self._deque: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def push(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._deque.append(entry)

    def tail(self, limit: int = 200) -> list[dict[str, Any]]:
        """返回最近若干条，新在前。"""
        lim = max(1, min(int(limit), 5000))
        with self._lock:
            chunk = list(self._deque)[-lim:]
        return list(reversed(chunk))

    def size(self) -> int:
        with self._lock:
            return len(self._deque)


class _RingBufferHandler(logging.Handler):
    def __init__(self, buf: AdminLogBuffer) -> None:
        super().__init__()
        self.setLevel(logging.INFO)
        self._buf = buf

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        try:
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        except Exception:
            ts = datetime.now(timezone.utc).isoformat()
        payload = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": (msg or "")[:8000],
        }
        self._buf.push(payload)
        if int(settings.ADMIN_LOG_RETENTION_DAYS) > 0:
            try:
                created = datetime.fromtimestamp(record.created, tz=timezone.utc)
            except Exception:
                created = datetime.now(timezone.utc)
            try_persist_admin_runtime_log(
                created_at=created,
                level=record.levelname,
                logger_name=record.name,
                message=payload["message"],
            )


def get_admin_log_buffer() -> AdminLogBuffer:
    global _buffer
    if _buffer is None:
        mx = max(100, int(settings.ADMIN_LOG_BUFFER_MAX))
        _buffer = AdminLogBuffer(maxlen=mx)
    return _buffer


def setup_admin_log_buffer() -> None:
    """挂载到 root logger，只安装一次。"""
    global _handler_installed
    if _handler_installed:
        return
    buf = get_admin_log_buffer()
    h = _RingBufferHandler(buf)
    h.setFormatter(
        logging.Formatter(
            fmt="%(message)s",
        )
    )
    root = logging.getLogger()
    if any(isinstance(x, _RingBufferHandler) for x in root.handlers):
        _handler_installed = True
        return
    root.addHandler(h)
    _handler_installed = True
