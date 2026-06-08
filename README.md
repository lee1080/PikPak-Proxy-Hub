# PikPak-Proxy-Hub

基于 PikPak 的资源中转加速平台（FastAPI + Next.js）。

本仓库的功能实现以《PikPak-Proxy-Hub 全栈开发白皮书.md》为蓝本，并补齐了本地调试与部署闭环（含默认管理员与强制改密策略）。

## 目录

- [功能概览](#功能概览)
- [本地开发（推荐）](#本地开发推荐)
  - [后端启动](#后端启动)
  - [前端启动](#前端启动)
  - [创建/重置管理员（终端）](#创建重置管理员终端)
- [Docker Compose 部署](#docker-compose-部署)
- [管理员策略（重要）](#管理员策略重要)
- [常见问题排查](#常见问题排查)

## 功能概览

- **多协议提交**：Magnet / HTTP / 社交链接（按白皮书的链接识别）；`.torrent` 上传会解析并转换为 Magnet 后进入任务队列
- **用户分级**：FREE / VIP / ADMIN（VIP/ADMIN 使用 PREMIUM 号池，FREE 使用 FREE 号池）
- **任务状态**：PENDING / SUBMITTED / DOWNLOADING / COMPLETED / FAILED / EXPIRED
- **直链取回**：`GET /api/v1/download/{job_id}` 返回 302 重定向到 PikPak 直链；直链过期后任务会被标记为 EXPIRED
- **后台管理**：账号池管理、用户等级管理、统计看板
- **PPHUB 目录**：每个号池账号在网盘根目录使用固定文件夹名 **PPHUB** 存放 Hub 离线文件；管理员「清空网盘」仅清理该文件夹。**调用 PikPak「创建离线任务」接口时**：若指定了目标目录（`parent_id`），则**不要**再传 `folder_type: "DOWNLOAD"`（二者并存时文件仍会落到默认「云下载 / My Pack」，等于忽略目录）；未指定目录时才使用 `DOWNLOAD` 表示走默认离线下载目录。

> 注意：PikPak API 可能屏蔽中国大陆 IP；在大陆网络环境下需要配置代理（见下文）。

## 本地开发（推荐）

### 快速启动（我只想跑起来）

如果你已经安装过依赖，直接开两个终端执行：

终端 1（后端）：

```bash
cd /Users/liyafei/Desktop/liyafei/pikpak/backend
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

终端 2（前端）：

```bash
cd /Users/liyafei/Desktop/liyafei/pikpak/frontend
npm run dev
```

验证：

- 后端健康检查：`http://127.0.0.1:8000/health`
- 前端页面：`http://localhost:3000/register`

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

后端接口文档：

- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

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
```

结束占用进程后再启动：

```bash
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
alembic upgrade head
```

如果你此前用自动建表跑过（`AUTO_CREATE_TABLES=true`），可能需要清理本地 sqlite 数据库后重建（文件：`backend/pikpak_hub.db`）。

### 4) 取回提示「直链生成中」或短时间内又在网盘看到重复离线文件

PikPak 取链接口有时 **`web_content_link` 为空但 `links` 里仍有下载 URL**；后端已同时解析二者。若长时间仍无链，任务在宽限期过后可能按「失效」逻辑 **自动重新离线**，会再占一次每日配额并在网盘多存一份——尽量避免在刚完成后频繁猛点取回；可将环境变量 `DOWNLOAD_WEBLINK_RETRY_ATTEMPTS`、`DOWNLOAD_WEBLINK_RETRY_DELAY_SECONDS` 调大以增加等待。

取回兼容：前端默认 **`POST /tasks/{id}/download-link?resolve_provider=true`**，响应中的 **`provider_url`** 为 PikPak 域名直链可直接新开页下载；若无则再请求 **`GET /api/v1/download/signed/{token}?format=json`** 得到 `{"url":"...","file_id":"...","files":[...]}`（磁力/种子结果为**文件夹**时，`files` 为多个候选文件的直链，默认只把 **≥ DOWNLOAD_FOLDER_MIN_SIZE_BYTES** 的文件纳入候选，没有则退化为包内最大文件；`DOWNLOAD_FOLDER_MIN_SIZE_BYTES=0` 关闭文件夹展开）。亦可仅用浏览器打开签名链接并加 **`?format=json`** 拿 JSON，避免依赖 302 跳转。

