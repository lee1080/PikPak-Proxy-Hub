# PikPak-Proxy-Hub

基于 PikPak 的资源中转加速平台（FastAPI + Next.js）。

本仓库的功能实现以《PikPak-Proxy-Hub 全栈开发白皮书.md》为蓝本，并补齐了本地调试与部署闭环（含默认管理员与强制改密策略）。可与 [LiteEmby](https://github.com/lee1080/LiteEmby) 联动，为 Emby/VidHub 客户端提供磁力/种子离线换链能力。

## 目录

- [功能概览](#功能概览)
- [本地开发（推荐）](#本地开发推荐)
  - [快速启动](#快速启动)
  - [后端启动](#后端启动)
  - [前端启动](#前端启动)
  - [创建/重置管理员（终端）](#创建重置管理员终端)
- [与 LiteEmby 联动](#与-liteemby-联动)
- [Docker Compose 部署](#docker-compose-部署)
- [管理员策略（重要）](#管理员策略重要)
- [常见问题排查](#常见问题排查)

## 功能概览

- **多协议提交**：Magnet / HTTP / 社交链接（按白皮书的链接识别）；`.torrent` 上传会解析并转换为 Magnet 后进入任务队列
- **用户分级**：FREE / VIP / ADMIN（VIP/ADMIN 使用 PREMIUM 号池，FREE 使用 FREE 号池）
- **号池调度**：Round Robin 轮询 + 剩余空间校验；轮询指针持久化于 `hub_meta` 表，**Hub 重启后不会总选第一个账号**
- **全站缓存**：相同 `content_key` 的已完成离线可复用，不重复消耗离线次数
- **任务状态**：PENDING / SUBMITTED / DOWNLOADING / COMPLETED / FAILED / EXPIRED
- **直链取回**：`GET /api/v1/download/{job_id}` 返回 302 重定向到 PikPak 直链；直链过期后任务会被标记为 EXPIRED
- **播放取链**：`GET /tasks/{job_id}/play-url` 支持文件夹型磁力展开、503/生成中轮询重试
- **后台管理**：账号池管理、用户等级管理、统计看板、运行日志
- **PPHUB 目录**：每个号池账号在网盘根目录使用固定文件夹名 **PPHUB** 存放 Hub 离线文件；管理员「清空网盘」仅清理该文件夹

> 注意：PikPak API 可能屏蔽中国大陆 IP；在大陆网络环境下需要配置代理（见下文）。

### 号池轮询说明

选号逻辑位于 `backend/app/services/scheduler.py`：

1. 从对应池（FREE / PREMIUM）中筛选 `ACTIVE` 且 `daily_tasks_left > 0`（或 `-1` 无限）的账号
2. 按账号 `id` 排序，从持久化指针位置开始 Round Robin
3. 跳过剩余空间不足的账号
4. 选中后将指针写入 `hub_meta`（key：`rr_pointer:FREE` / `rr_pointer:PREMIUM`）

FREE 池每日离线次数在 Hub 本地扣减，也可通过「同步 PikPak」从 about 接口对齐 PikPak 侧剩余次数。

## 本地开发（推荐）

### 快速启动

若已安装依赖，开两个终端：

**终端 1（后端）：**

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**终端 2（前端）：**

```bash
cd frontend
npm run dev
```

验证：

- 后端健康检查：`http://127.0.0.1:8000/health`
- 前端页面：`http://localhost:3000/register`
- API 文档：`http://127.0.0.1:8000/docs`

与 LiteEmby 联调时，LiteEmby 项目可执行 `./scripts/restart-dev.sh` 一键拉起 Hub + 前端 + LiteEmby。

### 后端启动

进入后端目录，创建虚拟环境并安装依赖：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

准备环境变量（开发用 SQLite）：

```bash
cp .env.example .env
```

初始化数据库（推荐用迁移，而不是自动建表）：

```bash
alembic upgrade head
```

启动后端：

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 前端启动

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

前端地址：`http://localhost:3000`

### 创建/重置管理员（终端）

#### 创建默认管理员（如果不存在）

```bash
cd backend
source .venv/bin/activate
python -m app.scripts.ensure_default_admin
```

#### 忘记密码：重置回默认账号密码（仅终端）

```bash
cd backend
source .venv/bin/activate
python -m app.scripts.reset_default_admin
```

## 与 LiteEmby 联动

| 组件 | 端口 | 说明 |
|------|------|------|
| PikPak Hub API | 8000 | LiteEmby 调用 `POST /tasks` 提交磁力 |
| PikPak 管理前端 | 3000 | 号池账号、任务、用户管理 |
| LiteEmby | 8096 | Emby 网关，管理台配置 Hub 地址与凭据 |

LiteEmby 播放 TORRENT/MAGNET 时：

1. `POST /api/v1/tasks` 提交磁力（或命中全站 `content_key` 缓存）
2. 轮询 `GET /tasks/{job_id}` 至 `COMPLETED`
3. `GET /tasks/{job_id}/play-url` 取直链
4. Hub 任务 `EXPIRED` 时 LiteEmby 会重新 submit，Hub 按轮询规则选新账号

在 LiteEmby 管理台 → **PikPak 联动** 填写 Hub 地址（如 `http://127.0.0.1:8000/api/v1`）及 Hub 用户 token。

## Docker Compose 部署

在根目录：

```bash
docker compose up -d --build
```

默认会启动：

- `frontend`：3000
- `backend`：8000
- `redis`：6379
- `db`（PostgreSQL）：5432

后端容器启动时会自动执行：

- `alembic upgrade head`
- `python -m app.scripts.ensure_default_admin`

## 管理员策略（重要）

部署完成后，默认管理员账号密码固定为：

- **username**：`admin`
- **password**：`admin123`

规则：

1. 管理员使用默认账号密码登录后，会被标记 **必须修改密码**（`must_change_password=true`）。
2. 在未修改密码前：
   - 管理后台页面会被强制跳转到 `/admin/force-change`
   - 所有管理员接口会返回 `必须先修改管理员密码`
3. 忘记密码**只能**通过终端执行重置命令恢复为 `admin/admin123`，并再次强制下次登录改密。

## 常见问题排查

### 1) 后端启动提示 8000 端口占用

```bash
lsof -iTCP:8000 -sTCP:LISTEN -n -P
kill <PID>
```

### 1.1) 前端启动提示 3000 端口占用

```bash
lsof -iTCP:3000 -sTCP:LISTEN -n -P
kill <PID>
```

### 2) 在大陆网络环境下无法调用 PikPak

配置代理（HTTP 代理），编辑 `backend/.env`：

```env
PIKPAK_PROXY=http://127.0.0.1:7890
```

### 3) 数据库与迁移冲突

开发期推荐始终使用：

```bash
cd backend
alembic upgrade head
```

若此前仅用自动建表（`AUTO_CREATE_TABLES=true`）跑过，可能需要清理本地 sqlite（`backend/pikpak_hub.db`）后重建。最新迁移包含 `hub_meta` 表（号池轮询指针）。

### 4) 离线次数总是消耗同一个号池账号

确认：

- 号池中有多个 `ACTIVE` 账号且 `daily_tasks_left > 0`
- 已执行 `alembic upgrade head`（含 `hub_meta` 迁移）
- Dev 环境下 `--reload` 频繁重启**不再**导致指针归零（已持久化）

可在管理台 → 账号管理查看各账号剩次数；连续提交不同磁力应看到 `pikpak_account_id` 轮换。

### 5) 取回提示「直链生成中」或短时间内又在网盘看到重复离线文件

PikPak 取链接口有时 **`web_content_link` 为空但 `links` 里仍有下载 URL**；后端已同时解析二者。若长时间仍无链，任务在宽限期过后可能按「失效」逻辑 **自动重新离线**，会再占一次每日配额并在网盘多存一份——尽量避免在刚完成后频繁猛点取回；可将环境变量 `DOWNLOAD_WEBLINK_RETRY_ATTEMPTS`、`DOWNLOAD_WEBLINK_RETRY_DELAY_SECONDS` 调大以增加等待。

取回兼容：前端默认 **`POST /tasks/{id}/download-link?resolve_provider=true`**，响应中的 **`provider_url`** 为 PikPak 域名直链可直接新开页下载；若无则再请求 **`GET /api/v1/download/signed/{token}?format=json`** 得到 `{"url":"...","file_id":"...","files":[...]}`（磁力/种子结果为**文件夹**时，`files` 为多个候选文件的直链，默认只把 **≥ DOWNLOAD_FOLDER_MIN_SIZE_BYTES** 的文件纳入候选，没有则退化为包内最大文件；`DOWNLOAD_FOLDER_MIN_SIZE_BYTES=0` 关闭文件夹展开）。亦可仅用浏览器打开签名链接并加 **`?format=json`** 拿 JSON，避免依赖 302 跳转。
