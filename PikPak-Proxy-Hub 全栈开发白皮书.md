# PikPak-Proxy-Hub 全栈开发白皮书 V5.0

> 本文档为项目的完整技术蓝图，涵盖后端架构、前端设计、数据库建模、API 规格、核心算法及部署方案。

---

## 第一章：项目概述

### 1.1 项目定位
PikPak-Proxy-Hub 是一个基于 PikPak 云盘离线能力的**资源中转加速平台**。用户提交资源链接，系统自动完成离线转存，并返回可直接高速取回的下载直链。

### 1.2 核心能力
- **多协议支持**：磁力 (Magnet)、种子 (.torrent)、HTTP/HTTPS 直链、社交媒体链接 (Twitter/TikTok/Telegram)、电驴 (ed2k)
- **用户分级**：免费用户使用免费号池轮询，VIP 用户使用付费会员号池
- **智能调度**：自动空间检测、FIFO 清理、号池轮询、容量预检
- **即用即删**：离线完成后自动清理，保持号池空间循环可用

### 1.3 技术栈总览

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| **后端框架** | FastAPI (Python 3.11+) | 原生异步，性能优秀 |
| **任务队列** | Redis + Celery (或 Arq) | 离线监听、定时清理 |
| **数据库** | PostgreSQL (生产) / SQLite (开发) | 用户、账号、任务持久化 |
| **ORM** | SQLAlchemy 2.0 + Alembic | 异步 ORM + 数据库迁移 |
| **缓存** | Redis | Token 缓存、速率限制、会话 |
| **前端框架** | Next.js 14 (App Router) | RSC + 流式渲染 |
| **UI 组件** | Shadcn UI + Radix UI | 无头组件，高度可定制 |
| **样式** | Tailwind CSS 3 | 原子化 CSS |
| **动画** | Framer Motion | 页面切换与微交互 |
| **状态管理** | TanStack Query (React Query) | 服务端状态 + 轮询 |
| **部署** | Docker Compose | 一键启动全部服务 |

---

## 第二章：后端架构设计

### 2.1 目录结构
```
backend/
├── app/
│   ├── main.py                 # FastAPI 入口，CORS、中间件
│   ├── config.py               # 环境变量与配置类
│   ├── database.py             # 数据库引擎与 Session
│   ├── models/                 # SQLAlchemy ORM 模型
│   │   ├── user.py             # 用户模型
│   │   ├── pikpak_account.py   # PikPak 账号模型
│   │   └── task.py             # 离线任务模型
│   ├── schemas/                # Pydantic 请求/响应模型
│   │   ├── user.py
│   │   ├── account.py
│   │   └── task.py
│   ├── routers/                # API 路由
│   │   ├── auth.py             # 注册/登录/刷新
│   │   ├── tasks.py            # 任务提交/查询/下载
│   │   └── admin.py            # 管理员操作
│   ├── services/               # 业务逻辑层
│   │   ├── pikpak_driver.py    # PikPak API 封装
│   │   ├── link_parser.py      # 链接解析引擎
│   │   ├── scheduler.py        # 号池调度器
│   │   └── cleanup.py          # 空间清理服务
│   ├── workers/                # Celery 异步任务
│   │   ├── offline_worker.py   # 离线提交 & 轮询
│   │   └── cleanup_worker.py   # 定时清理
│   └── utils/
│       ├── security.py         # JWT、密码哈希
│       └── exceptions.py       # 自定义异常
├── alembic/                    # 数据库迁移
├── requirements.txt
├── Dockerfile
└── .env.example
```

### 2.2 配置管理 (`config.py`)
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/pikpak_hub"
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    # JWT
    JWT_SECRET_KEY: str = "your-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # PikPak 相关
    PIKPAK_CLIENT_ID: str = "YNxT9w7GMdWvEOKa"
    PIKPAK_CLIENT_SECRET: str = "dbw2OtmVEeuUvIptb1Coyg"
    # 清理策略
    FILE_TTL_HOURS: int = 24              # 文件过期时间
    POLL_INTERVAL_SECONDS: int = 8        # 离线进度轮询间隔
    FREE_MAX_FILE_SIZE_BYTES: int = 6_442_450_944  # 6GB
    # 轮询
    FREE_DAILY_TASK_LIMIT: int = 3        # 免费账号每日离线次数

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## 第三章：PikPak API 驱动层详解

### 3.1 API 端点清单

PikPak 没有官方公开文档，以下接口基于逆向分析和开源社区（`pikpakapi`、`rclone`）整理。

| 功能 | 方法 | 端点 | 说明 |
|------|------|------|------|
| **用户登录** | POST | `https://user.mypikpak.com/v1/auth/signin` | 返回 access_token / refresh_token |
| **Token 刷新** | POST | `https://user.mypikpak.com/v1/auth/token` | 用 refresh_token 换新 access_token |
| **获取用户信息** | GET | `https://user.mypikpak.com/v1/user/me` | 包含 VIP 状态 |
| **查询存储配额** | GET | `https://api-drive.mypikpak.com/drive/v1/about` | 返回 quota.usage / quota.limit |
| **创建离线任务** | POST | `https://api-drive.mypikpak.com/drive/v1/files` | 提交磁力/HTTP 等链接 |
| **查询离线列表** | GET | `https://api-drive.mypikpak.com/drive/v1/tasks` | 获取任务状态与进度 |
| **获取文件列表** | GET | `https://api-drive.mypikpak.com/drive/v1/files` | 浏览云盘目录 |
| **获取下载地址** | GET | `https://api-drive.mypikpak.com/drive/v1/files/{file_id}` | 返回 web_content_link (直链) |
| **删除文件** | POST | `https://api-drive.mypikpak.com/drive/v1/files:batchTrash` | 批量移入回收站 |
| **清空回收站** | PATCH | `https://api-drive.mypikpak.com/drive/v1/files/trash:empty` | 彻底释放空间 |

### 3.2 请求头规范
```python
COMMON_HEADERS = {
    "User-Agent": "protocolversion/200 clientid/{client_id}",
    "X-Device-ID": "{device_id}",          # UUID v4，每个账号固定
    "X-Captcha-Token": "{captcha_token}",   # 部分操作需要验证码 Token
    "Content-Type": "application/json",
}
# 登录后追加
AUTH_HEADERS = {
    "Authorization": "Bearer {access_token}",
}
```

### 3.3 登录流程
```python
# POST https://user.mypikpak.com/v1/auth/signin
login_payload = {
    "client_id": settings.PIKPAK_CLIENT_ID,
    "client_secret": settings.PIKPAK_CLIENT_SECRET,
    "username": account.email,
    "password": account.password,
}
# 响应
{
    "access_token": "eyJ...",
    "refresh_token": "1/xxx...",
    "sub": "用户ID",
    "expires_in": 7200     # 2小时有效期
}
```

### 3.4 创建离线任务
```python
# POST https://api-drive.mypikpak.com/drive/v1/files
# 磁力链接
magnet_payload = {
    "kind": "drive#file",
    "upload_type": "UPLOAD_TYPE_URL",
    "url": {"url": "magnet:?xt=urn:btih:xxxxx"},
    "folder_type": "DOWNLOAD",
}
# HTTP 直链
http_payload = {
    "kind": "drive#file",
    "upload_type": "UPLOAD_TYPE_URL",
    "url": {"url": "https://example.com/file.zip"},
    "folder_type": "DOWNLOAD",
}
# 指定保存目录（例如根目录下 Hub 专用文件夹 PPHUB 的 folder_id）时：
# 请求体中应设置 parent_id，且「不要」再携带 folder_type: "DOWNLOAD"。
# 若仍传 DOWNLOAD，PikPak 会将文件落到默认「云下载 / My Pack」，parent_id 实际不生效（与 rclone 行为一致：有 parent_id 时 FolderType 应为空）。
into_folder_payload = {
    "kind": "drive#file",
    "upload_type": "UPLOAD_TYPE_URL",
    "url": {"url": "https://example.com/file.zip"},
    "parent_id": "<PPHUB_FOLDER_ID>",
}
# 响应
{
    "file": {"id": "FileID123", "name": "movie.mkv", "size": "1073741824"},
    "task": {"id": "TaskID456", "phase": "PHASE_TYPE_RUNNING", "progress": 0}
}
```

### 3.5 查询任务进度
```python
# GET https://api-drive.mypikpak.com/drive/v1/tasks/{task_id}
# 响应
{
    "id": "TaskID456",
    "phase": "PHASE_TYPE_COMPLETE",  # RUNNING / COMPLETE / ERROR
    "progress": 100,
    "file_id": "FileID123",
    "file_name": "movie.mkv",
    "file_size": "1073741824",
    "message": ""  # 错误时有值
}
```

### 3.6 获取下载直链
```python
# GET https://api-drive.mypikpak.com/drive/v1/files/{file_id}?_magic=2021&thumbnail_size=SIZE_LARGE
# 响应（关键字段）
{
    "id": "FileID123",
    "web_content_link": "https://dl-a]..mypikpak.com/...?sig=xxx&t=1234",  # 有效期约 15-60 分钟
    "medias": [{"link": {"url": "https://..."}}]   # 视频文件会有转码流
}
```

### 3.7 驱动层核心代码结构 (`pikpak_driver.py`)
```python
import httpx
from app.config import settings

class PikPakDriver:
    API_BASE = "https://api-drive.mypikpak.com"
    USER_BASE = "https://user.mypikpak.com"

    def __init__(self, email: str, password: str, device_id: str,
                 refresh_token: str | None = None):
        self.email = email
        self.password = password
        self.device_id = device_id
        self.access_token: str | None = None
        self.refresh_token = refresh_token
        self._client = httpx.AsyncClient(timeout=30)

    async def login(self) -> dict:
        """登录并获取 Token"""
        resp = await self._client.post(f"{self.USER_BASE}/v1/auth/signin", json={
            "client_id": settings.PIKPAK_CLIENT_ID,
            "client_secret": settings.PIKPAK_CLIENT_SECRET,
            "username": self.email,
            "password": self.password,
        })
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        return data

    async def refresh(self) -> dict:
        """用 refresh_token 换取新的 access_token"""
        resp = await self._client.post(f"{self.USER_BASE}/v1/auth/token", json={
            "client_id": settings.PIKPAK_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        })
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        return data

    @property
    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "X-Device-ID": self.device_id,
            "Content-Type": "application/json",
        }

    async def get_quota(self) -> dict:
        """获取存储配额 {usage: int, limit: int}"""
        resp = await self._client.get(
            f"{self.API_BASE}/drive/v1/about", headers=self._auth_headers
        )
        quota = resp.json().get("quota", {})
        return {"usage": int(quota.get("usage", 0)),
                "limit": int(quota.get("limit", 0))}

    async def add_offline_task(self, url: str, folder_id: str = "") -> dict:
        """提交离线下载任务。指定 parent_id 时不要带 folder_type=DOWNLOAD。"""
        payload = {
            "kind": "drive#file",
            "upload_type": "UPLOAD_TYPE_URL",
            "url": {"url": url},
        }
        if folder_id:
            payload["parent_id"] = folder_id
        else:
            payload["folder_type"] = "DOWNLOAD"
        resp = await self._client.post(
            f"{self.API_BASE}/drive/v1/files",
            json=payload, headers=self._auth_headers
        )
        return resp.json()

    async def get_task_status(self, task_id: str) -> dict:
        """查询离线任务进度"""
        resp = await self._client.get(
            f"{self.API_BASE}/drive/v1/tasks/{task_id}",
            headers=self._auth_headers
        )
        return resp.json()

    async def get_download_url(self, file_id: str) -> str:
        """获取文件下载直链（有效期约 15-60 分钟）"""
        resp = await self._client.get(
            f"{self.API_BASE}/drive/v1/files/{file_id}",
            params={"_magic": "2021", "thumbnail_size": "SIZE_LARGE"},
            headers=self._auth_headers
        )
        return resp.json().get("web_content_link", "")

    async def delete_files(self, file_ids: list[str]) -> bool:
        """批量删除文件（移入回收站）"""
        resp = await self._client.post(
            f"{self.API_BASE}/drive/v1/files:batchTrash",
            json={"ids": file_ids}, headers=self._auth_headers
        )
        return resp.status_code == 200

    async def empty_trash(self) -> bool:
        """清空回收站，彻底释放空间"""
        resp = await self._client.patch(
            f"{self.API_BASE}/drive/v1/files/trash:empty",
            json={}, headers=self._auth_headers
        )
        return resp.status_code == 200
```

---

## 第四章：权限与鉴权系统

### 4.1 用户角色定义
```python
from enum import Enum

class UserLevel(str, Enum):
    FREE = "FREE"       # 免费用户：使用免费号池
    VIP = "VIP"         # VIP 用户：使用付费号池
    ADMIN = "ADMIN"     # 管理员：全部权限
```

### 4.2 JWT 双 Token 机制
- **Access Token**：有效期 1 小时，用于接口鉴权
- **Refresh Token**：有效期 7 天，用于无感续期

```python
# utils/security.py
from jose import jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(user_id: str, level: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "level": level, "exp": expire},
                      settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": user_id, "type": "refresh", "exp": expire},
                      settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
```

### 4.3 权限守卫中间件
```python
# routers/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(cred: HTTPAuthorizationCredentials = Depends(security)):
    payload = jwt.decode(cred.credentials, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])
    user = await get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user

async def require_admin(user = Depends(get_current_user)):
    if user.level != UserLevel.ADMIN:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user

async def require_vip_or_above(user = Depends(get_current_user)):
    if user.level not in (UserLevel.VIP, UserLevel.ADMIN):
        raise HTTPException(status_code=403, detail="需要 VIP 权限")
    return user
```

---

## 第五章：数据库设计

### 5.1 ER 关系图
```
┌──────────┐       ┌─────────────────┐       ┌──────────┐
│  users   │       │  pikpak_accounts │       │  tasks   │
├──────────┤       ├─────────────────┤       ├──────────┤
│ id (PK)  │──┐    │ id (PK)         │──┐    │ id (PK)  │
│ username │  │    │ email           │  │    │ user_id  │──→ users.id
│ password │  │    │ password_enc    │  │    │ account_id│──→ pikpak_accounts.id
│ level    │  │    │ refresh_token   │  │    │ source_url│
│ created  │  │    │ device_id       │  │    │ file_id  │
└──────────┘  │    │ pool_type       │  │    │ file_name│
              │    │ quota_total     │  │    │ file_size│
              │    │ quota_used      │  │    │ status   │
              │    │ daily_tasks_left│  │    │ progress │
              │    │ status          │  │    │ download_url│
              │    │ last_synced_at  │  │    │ error_msg│
              │    └─────────────────┘  │    │ expire_at│
              │                         │    │ created  │
              └─────────────────────────┘    └──────────┘
```

### 5.2 完整 DDL
```sql
-- 用户表
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(64) UNIQUE NOT NULL,
    email         VARCHAR(128) UNIQUE,
    password_hash VARCHAR(128) NOT NULL,
    level         VARCHAR(16) NOT NULL DEFAULT 'FREE'
                  CHECK (level IN ('FREE', 'VIP', 'ADMIN')),
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);

-- PikPak 账号表
CREATE TABLE pikpak_accounts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email            VARCHAR(128) UNIQUE NOT NULL,
    password_enc     TEXT NOT NULL,                    -- AES 加密存储
    refresh_token    TEXT,
    device_id        VARCHAR(64) NOT NULL,             -- 设备标识 UUID
    pool_type        VARCHAR(16) NOT NULL DEFAULT 'FREE'
                     CHECK (pool_type IN ('FREE', 'PREMIUM')),
    quota_total      BIGINT DEFAULT 6442450944,        -- 默认 6GB
    quota_used       BIGINT DEFAULT 0,
    daily_tasks_left INT DEFAULT 3,                    -- 每日剩余离线次数
    status           VARCHAR(16) DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'SUSPENDED', 'TOKEN_EXPIRED', 'BANNED')),
    last_synced_at   TIMESTAMP,                        -- 上次同步配额的时间
    created_at       TIMESTAMP DEFAULT NOW()
);

-- 离线任务表
CREATE TABLE tasks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pikpak_account_id UUID REFERENCES pikpak_accounts(id),
    source_url        TEXT NOT NULL,                    -- 原始链接
    link_type         VARCHAR(16) NOT NULL
                      CHECK (link_type IN ('MAGNET', 'TORRENT', 'HTTP', 'SOCIAL', 'ED2K')),
    pikpak_task_id    VARCHAR(64),                      -- PikPak 侧任务 ID
    file_id           VARCHAR(64),                      -- PikPak 侧文件 ID
    file_name         VARCHAR(512),
    file_size         BIGINT DEFAULT 0,
    status            VARCHAR(16) NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING','SUBMITTED','DOWNLOADING','COMPLETED','FAILED','EXPIRED')),
    progress          INT DEFAULT 0,                    -- 0-100
    error_message     TEXT,
    expire_at         TIMESTAMP,                        -- 直链过期时间
    created_at        TIMESTAMP DEFAULT NOW(),
    completed_at      TIMESTAMP
);

-- 索引
CREATE INDEX idx_tasks_user ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_account ON tasks(pikpak_account_id);
CREATE INDEX idx_accounts_pool ON pikpak_accounts(pool_type, status);
```

---

## 第六章：链接解析引擎 (Link Parser)

### 6.1 协议识别正则表达式
```python
# services/link_parser.py
import re
from enum import Enum

class LinkType(str, Enum):
    MAGNET = "MAGNET"
    TORRENT = "TORRENT"
    HTTP = "HTTP"
    SOCIAL = "SOCIAL"
    ED2K = "ED2K"
    UNKNOWN = "UNKNOWN"

LINK_PATTERNS = {
    LinkType.MAGNET:  re.compile(r"^magnet:\?xt=urn:[a-z0-9]+:[a-zA-Z0-9]{32,}", re.I),
    LinkType.ED2K:    re.compile(r"^ed2k://\|file\|", re.I),
    LinkType.TORRENT: re.compile(r"\.torrent(\?.*)?$", re.I),
    LinkType.SOCIAL:  re.compile(
        r"(twitter\.com|x\.com|tiktok\.com|t\.co|facebook\.com|"
        r"instagram\.com|t\.me|telegram\.me)", re.I
    ),
    LinkType.HTTP:    re.compile(r"^https?://", re.I),
}

def detect_link_type(url: str) -> LinkType:
    """按优先级匹配链接类型"""
    for link_type, pattern in LINK_PATTERNS.items():
        if pattern.search(url):
            return link_type
    return LinkType.UNKNOWN
```

### 6.2 文件大小预估
```python
import httpx

async def estimate_file_size(url: str, link_type: LinkType) -> int | None:
    """尝试通过 HEAD 请求获取文件大小（仅 HTTP 直链有效）"""
    if link_type != LinkType.HTTP:
        return None  # 磁力/种子无法预估，依赖 PikPak 返回值
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
            resp = await c.head(url)
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:
        return None
```

### 6.3 Torrent 文件处理流程
1. 用户上传 `.torrent` 文件到后端 `POST /api/v1/tasks/upload-torrent`
2. 后端将文件保存至临时目录 `/tmp/torrents/{uuid}.torrent`
3. 使用 `python-libtorrent` 或 `torrent-parser` 解析种子，提取文件列表与总大小
4. 调用 PikPak 的种子上传接口（先上传种子文件，再触发离线）
5. 任务完成后删除临时种子文件

---

## 第七章：号池调度与智能清理算法

### 7.1 号池轮询算法 (Round Robin)
```python
# services/scheduler.py
from sqlalchemy import select
from app.models.pikpak_account import PikPakAccount

class PoolScheduler:
    def __init__(self, db_session):
        self.db = db_session
        self._free_index = 0    # 轮询指针
        self._vip_index = 0

    async def pick_account(self, pool_type: str, required_size: int) -> PikPakAccount | None:
        """从指定池中选取可用账号"""
        accounts = await self.db.execute(
            select(PikPakAccount)
            .where(PikPakAccount.pool_type == pool_type)
            .where(PikPakAccount.status == "ACTIVE")
            .where(PikPakAccount.daily_tasks_left > 0)
            .order_by(PikPakAccount.id)
        )
        pool = accounts.scalars().all()
        if not pool:
            return None

        # 轮询遍历，最多遍历一圈
        idx = self._free_index if pool_type == "FREE" else self._vip_index
        for i in range(len(pool)):
            candidate = pool[(idx + i) % len(pool)]
            free_space = candidate.quota_total - candidate.quota_used
            if free_space >= required_size:
                # 更新指针
                if pool_type == "FREE":
                    self._free_index = (idx + i + 1) % len(pool)
                else:
                    self._vip_index = (idx + i + 1) % len(pool)
                return candidate
        return None  # 所有账号空间均不足
```

### 7.2 空间驱动清理 (Space-On-Demand)
```python
# services/cleanup.py
from sqlalchemy import select, asc
from app.models.task import Task

class CleanupService:
    def __init__(self, db_session, pikpak_driver):
        self.db = db_session
        self.driver = pikpak_driver

    async def ensure_space(self, account_id: str, required_bytes: int) -> bool:
        """确保账号有足够空间，不足则按 FIFO 删除旧文件"""
        account = await self.db.get(PikPakAccount, account_id)
        free_space = account.quota_total - account.quota_used

        if free_space >= required_bytes:
            return True

        # 查询该账号下已完成的任务（按时间升序 = 最旧优先）
        old_tasks = await self.db.execute(
            select(Task)
            .where(Task.pikpak_account_id == account_id)
            .where(Task.status == "COMPLETED")
            .where(Task.file_id.isnot(None))
            .order_by(asc(Task.completed_at))
        )
        old_tasks = old_tasks.scalars().all()

        freed = 0
        to_delete_ids = []
        for task in old_tasks:
            to_delete_ids.append(task.file_id)
            freed += task.file_size
            task.status = "EXPIRED"
            if (free_space + freed) >= required_bytes:
                break

        if not to_delete_ids:
            return False

        # 调用 PikPak API 删除文件
        await self.driver.delete_files(to_delete_ids)
        await self.driver.empty_trash()

        # 更新数据库中的配额
        account.quota_used = max(0, account.quota_used - freed)
        await self.db.commit()
        return (free_space + freed) >= required_bytes
```

### 7.3 TTL 定时清理 (Celery Beat)
```python
# workers/cleanup_worker.py
from celery import Celery
from celery.schedules import crontab
from datetime import datetime

app = Celery("cleanup", broker=settings.REDIS_URL)

# 每小时执行一次过期文件清理
app.conf.beat_schedule = {
    "cleanup-expired-files": {
        "task": "workers.cleanup_worker.cleanup_expired",
        "schedule": crontab(minute=0),   # 每小时整点
    },
    "reset-daily-task-limits": {
        "task": "workers.cleanup_worker.reset_daily_limits",
        "schedule": crontab(hour=0, minute=5),  # 每天 00:05 重置
    },
}

@app.task
async def cleanup_expired():
    """删除所有超过 TTL 的已完成文件"""
    now = datetime.utcnow()
    expired_tasks = await db.execute(
        select(Task)
        .where(Task.status == "COMPLETED")
        .where(Task.expire_at < now)
        .where(Task.file_id.isnot(None))
    )
    for task in expired_tasks.scalars():
        driver = await get_driver_for_account(task.pikpak_account_id)
        await driver.delete_files([task.file_id])
        task.status = "EXPIRED"
    await db.commit()

@app.task
async def reset_daily_limits():
    """每日重置所有免费账号的离线次数为 3"""
    await db.execute(
        update(PikPakAccount)
        .where(PikPakAccount.pool_type == "FREE")
        .values(daily_tasks_left=3, status="ACTIVE")
    )
    await db.commit()
```

---

## 第八章：RESTful API 接口规格

### 8.1 认证接口

#### `POST /api/v1/auth/register` — 用户注册
```json
// 请求
{ "username": "user1", "email": "u@mail.com", "password": "abc123" }
// 成功响应 201
{ "id": "uuid", "username": "user1", "level": "FREE" }
```

#### `POST /api/v1/auth/login` — 用户登录
```json
// 请求
{ "username": "user1", "password": "abc123" }
// 成功响应 200
{ "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer",
  "user": { "id": "uuid", "username": "user1", "level": "FREE" } }
```

#### `POST /api/v1/auth/refresh` — 刷新 Token
```json
// 请求
{ "refresh_token": "eyJ..." }
// 成功响应 200
{ "access_token": "eyJ_new...", "refresh_token": "eyJ_new..." }
```

### 8.2 任务接口

#### `POST /api/v1/tasks` — 提交离线任务
```json
// 请求 (Header: Authorization: Bearer xxx)
{ "url": "magnet:?xt=urn:btih:abc123..." }
// 成功响应 202
{ "job_id": "uuid", "link_type": "MAGNET", "status": "PENDING",
  "message": "任务已提交，正在排队" }
// 错误响应 413 (免费用户文件超限)
{ "error": "FILE_TOO_LARGE", "message": "文件大小超过 6GB，请升级 VIP 账户",
  "estimated_size": 8589934592, "limit": 6442450944 }
```

#### `GET /api/v1/tasks` — 查询我的任务列表
```json
// 响应 200
{ "tasks": [
    { "job_id": "uuid", "file_name": "movie.mkv", "file_size": 1073741824,
      "status": "COMPLETED", "progress": 100,
      "download_url": "/api/v1/download/uuid", "created_at": "..." },
    { "job_id": "uuid2", "status": "DOWNLOADING", "progress": 45 }
  ],
  "total": 2 }
```

#### `GET /api/v1/tasks/{job_id}` — 查询单个任务详情
```json
// 响应 200
{ "job_id": "uuid", "source_url": "magnet:...", "link_type": "MAGNET",
  "file_name": "movie.mkv", "file_size": 1073741824,
  "status": "COMPLETED", "progress": 100,
  "pool_type": "FREE", "created_at": "...", "completed_at": "..." }
```

#### `GET /api/v1/download/{job_id}` — 获取直链下载（302 重定向）
```
// 成功：HTTP 302 → Location: https://dl-xxx.mypikpak.com/...?sig=xxx
// 失败 404：任务不存在或已过期
```

### 8.3 管理员接口

#### `GET /api/v1/admin/accounts` — 查看号池状态
```json
// 响应 200
{ "accounts": [
    { "id": "uuid", "email": "pp1@mail.com", "pool_type": "FREE",
      "status": "ACTIVE", "quota_total": 6442450944, "quota_used": 2147483648,
      "quota_percent": 33.3, "daily_tasks_left": 2 },
    { "id": "uuid2", "email": "vip@mail.com", "pool_type": "PREMIUM",
      "status": "ACTIVE", "quota_total": 10995116277760, "quota_used": 0,
      "daily_tasks_left": -1 }
  ] }
```

#### `POST /api/v1/admin/accounts` — 添加 PikPak 账号
```json
// 请求
{ "email": "new@mail.com", "password": "pass123", "pool_type": "FREE" }
// 成功响应 201（后端会自动登录验证）
{ "id": "uuid", "email": "new@mail.com", "pool_type": "FREE",
  "status": "ACTIVE", "message": "账号验证成功，已加入免费号池" }
```

#### `DELETE /api/v1/admin/accounts/{id}` — 移除 PikPak 账号

#### `PATCH /api/v1/admin/users/{id}` — 修改用户等级
```json
// 请求
{ "level": "VIP" }
// 成功响应 200
{ "id": "uuid", "username": "user1", "level": "VIP" }
```

#### `GET /api/v1/admin/stats` — 全局统计
```json
{ "total_users": 120, "vip_users": 15,
  "free_pool": { "count": 10, "active": 8, "avg_usage_percent": 45.2 },
  "premium_pool": { "count": 2, "active": 2, "avg_usage_percent": 12.1 },
  "tasks_today": 89, "tasks_completed": 76, "tasks_failed": 3 }
```

---

## 第九章：前端设计与实现

### 9.1 项目目录结构
```
frontend/
├── app/
│   ├── layout.tsx              # 全局布局（暗色主题、字体）
│   ├── page.tsx                # 首页：链接提交 + 任务列表
│   ├── login/page.tsx          # 登录页
│   ├── register/page.tsx       # 注册页
│   ├── admin/
│   │   ├── layout.tsx          # 管理后台布局（侧边栏）
│   │   ├── page.tsx            # 统计看板
│   │   ├── accounts/page.tsx   # 号池管理
│   │   └── users/page.tsx      # 用户管理
│   └── globals.css             # Tailwind 入口 + 自定义变量
├── components/
│   ├── ui/                     # Shadcn UI 组件
│   │   ├── button.tsx
│   │   ├── input.tsx
│   │   ├── card.tsx
│   │   ├── badge.tsx
│   │   ├── dialog.tsx
│   │   ├── table.tsx
│   │   ├── progress.tsx
│   │   └── toast.tsx
│   ├── link-submit-box.tsx     # 链接提交组件
│   ├── task-card.tsx           # 单个任务卡片
│   ├── task-list.tsx           # 任务列表（实时轮询）
│   ├── pool-chart.tsx          # 号池容量环形图
│   ├── account-table.tsx       # 账号管理表格
│   └── navbar.tsx              # 顶栏导航
├── lib/
│   ├── api.ts                  # Axios 封装 + 拦截器
│   ├── auth.ts                 # Token 管理
│   └── utils.ts                # 工具函数
├── hooks/
│   ├── use-tasks.ts            # TanStack Query 任务轮询 Hook
│   └── use-auth.ts             # 鉴权 Hook
├── tailwind.config.ts
├── next.config.js
└── package.json
```

### 9.2 视觉设计系统

#### 色彩方案 (CSS Variables)
```css
/* globals.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --background:    222 47% 6%;      /* #0A0E1A 深蓝黑 */
  --foreground:    210 40% 92%;     /* #E8EDF5 浅灰白 */
  --card:          222 30% 10%;     /* #141824 卡片底色 */
  --card-foreground: 210 40% 92%;
  --primary:       217 91% 60%;     /* #3B82F6 科技蓝 */
  --primary-hover: 217 91% 50%;
  --accent:        142 71% 45%;     /* #22C55E 成功绿 */
  --destructive:   0 84% 60%;      /* #EF4444 警告红 */
  --warning:       38 92% 50%;     /* #F59E0B 橙色 */
  --muted:         215 20% 25%;
  --border:        215 20% 18%;
  --ring:          217 91% 60%;
  --radius:        0.75rem;
}
```

#### 毛玻璃卡片样式
```css
.glass-card {
  @apply bg-white/5 backdrop-blur-xl border border-white/10
         rounded-xl shadow-2xl shadow-black/20
         transition-all duration-300 hover:border-white/20
         hover:shadow-primary/5;
}
```

#### 动态网格背景
```css
.grid-background {
  background-image:
    linear-gradient(rgba(59, 130, 246, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(59, 130, 246, 0.03) 1px, transparent 1px);
  background-size: 64px 64px;
  animation: grid-drift 20s linear infinite;
}
@keyframes grid-drift {
  0% { background-position: 0 0; }
  100% { background-position: 64px 64px; }
}
```

### 9.3 核心组件实现

#### 链接提交框 (`link-submit-box.tsx`)
```tsx
"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Send, Loader2, Link2, Magnet } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

const ICON_MAP: Record<string, React.ElementType> = {
  MAGNET: Magnet, HTTP: Link2, SOCIAL: Link2, ED2K: Link2,
};

export function LinkSubmitBox() {
  const [url, setUrl] = useState("");
  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: (url: string) => api.post("/tasks", { url }),
    onSuccess: () => {
      setUrl("");
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  return (
    <motion.div initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="glass-card p-6 max-w-2xl mx-auto">
      <div className="flex gap-3">
        <input value={url} onChange={e => setUrl(e.target.value)}
               placeholder="粘贴磁力链接、HTTP 地址或社交媒体链接..."
               className="flex-1 bg-white/5 border border-white/10 rounded-lg
                          px-4 py-3 text-white placeholder:text-white/30
                          focus:outline-none focus:ring-2 focus:ring-primary/50"/>
        <button onClick={() => submit.mutate(url)}
                disabled={!url || submit.isPending}
                className="bg-primary hover:bg-primary-hover text-white
                           px-6 py-3 rounded-lg flex items-center gap-2
                           disabled:opacity-50 transition-colors">
          {submit.isPending ? <Loader2 className="animate-spin" size={18}/> : <Send size={18}/>}
          提交
        </button>
      </div>
      {submit.isError && (
        <p className="text-destructive text-sm mt-2">
          {(submit.error as any)?.response?.data?.message || "提交失败"}
        </p>
      )}
    </motion.div>
  );
}
```

#### 任务卡片 (`task-card.tsx`)
```tsx
"use client";
import { motion } from "framer-motion";
import { Download, Clock, CheckCircle, XCircle, Loader } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";

const STATUS_CONFIG = {
  PENDING:     { icon: Clock,       color: "text-warning",     label: "排队中" },
  SUBMITTED:   { icon: Clock,       color: "text-warning",     label: "已提交" },
  DOWNLOADING: { icon: Loader,      color: "text-primary",     label: "离线中" },
  COMPLETED:   { icon: CheckCircle, color: "text-accent",      label: "已完成" },
  FAILED:      { icon: XCircle,     color: "text-destructive", label: "失败" },
  EXPIRED:     { icon: Clock,       color: "text-muted",       label: "已过期" },
};

export function TaskCard({ task }: { task: TaskItem }) {
  const cfg = STATUS_CONFIG[task.status];
  const Icon = cfg.icon;

  return (
    <motion.div layout initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="glass-card p-4 flex items-center gap-4">
      <Icon className={`${cfg.color} shrink-0`} size={24} />
      <div className="flex-1 min-w-0">
        <p className="text-white font-medium truncate">{task.file_name || "解析中..."}</p>
        <div className="flex items-center gap-2 mt-1">
          <Badge variant="outline" className="text-xs">{task.link_type}</Badge>
          <span className="text-white/40 text-xs">
            {task.file_size ? formatBytes(task.file_size) : "--"}
          </span>
        </div>
        {task.status === "DOWNLOADING" && (
          <Progress value={task.progress} className="mt-2 h-1.5" />
        )}
      </div>
      {task.status === "COMPLETED" && (
        <a href={`/api/v1/download/${task.job_id}`}
           className="bg-accent/20 text-accent hover:bg-accent/30
                      px-4 py-2 rounded-lg flex items-center gap-2 transition-colors">
          <Download size={16} /> 取回
        </a>
      )}
    </motion.div>
  );
}
```

#### 实时任务轮询 Hook (`use-tasks.ts`)
```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useTasks() {
  return useQuery({
    queryKey: ["tasks"],
    queryFn: () => api.get("/tasks").then(r => r.data),
    refetchInterval: (query) => {
      // 如果有正在进行的任务，每 3 秒刷新；否则每 30 秒
      const tasks = query.state.data?.tasks ?? [];
      const hasActive = tasks.some(
        (t: any) => ["PENDING", "SUBMITTED", "DOWNLOADING"].includes(t.status)
      );
      return hasActive ? 3000 : 30000;
    },
  });
}
```

### 9.4 管理员看板

#### 号池容量环形图 (`pool-chart.tsx`)
使用 `recharts` 库绘制环形图，实时展示每个 PikPak 账号的空间占用率。

#### 管理员页面核心功能
| 页面 | 功能 | 交互 |
|------|------|------|
| **统计看板** | 总用户数、今日任务数、号池健康度 | 自动刷新 |
| **号池管理** | 表格展示所有 PikPak 账号 | 添加/删除账号、手动同步配额 |
| **用户管理** | 用户列表、等级修改 | 一键升级 VIP |

---

## 第十章：部署方案

### 10.1 Docker Compose
```yaml
# docker-compose.yml
version: "3.9"
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    env_file: ./backend/.env
    depends_on: [redis, db]
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000

  celery-worker:
    build: ./backend
    env_file: ./backend/.env
    depends_on: [redis, db]
    command: celery -A app.workers.celery_app worker --loglevel=info

  celery-beat:
    build: ./backend
    env_file: ./backend/.env
    depends_on: [redis]
    command: celery -A app.workers.celery_app beat --loglevel=info

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000/api/v1
    depends_on: [backend]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: ["redis_data:/data"]

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: pikpak_hub
      POSTGRES_USER: pikpak
      POSTGRES_PASSWORD: your_password
    ports: ["5432:5432"]
    volumes: ["pg_data:/var/lib/postgresql/data"]

volumes:
  redis_data:
  pg_data:
```

### 10.2 启动命令
```bash
# 一键启动全部服务
docker compose up -d --build

# 执行数据库迁移
docker compose exec backend alembic upgrade head

# 创建管理员账号
docker compose exec backend python -m app.scripts.create_admin
```

### 10.3 网络要求
> **重要**：PikPak API 屏蔽中国大陆 IP。部署服务器必须具备海外网络访问能力（香港/新加坡/日本等），或配置 HTTP 代理。

```python
# config.py 中增加代理配置
PIKPAK_PROXY: str | None = "http://proxy:7890"  # 可选
```

---

