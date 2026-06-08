from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Optional, cast

import httpx

from app.config import settings
from app.services.pikpak_captcha import SignProfile, shield_meta_for_login

log = logging.getLogger(__name__)

# 每个 PikPak 账号在网盘根目录使用固定名称的 Hub 专用文件夹；离线任务写入其中，管理员清空仅清理该目录。
PIKPAK_HUB_FOLDER_NAME = "PPHUB"


def _pikpak_reserved_trash_denied_message(text: str) -> bool:
    """PikPak 根目录等系统占位目录 batchTrash 后任务 phase 会返回类似文案。"""
    m = (text or "").lower()
    return "operating system folder" in m or (
        "system folder" in m and "not allowed" in m
    )


def _parse_pikpak_file_size(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, str):
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    return 0


def _pick_download_url_from_file_json(data: Any) -> str:
    """
    PikPak `GET /drive/v1/files/{id}` 有时 web_content_link 为空，但 links['application/octet-stream'].url 可用（见 rclone）。
    仅依赖 web_content_link 会导致取回误判为空 → 宽限期 503 → 超时后触发重新离线，重复消耗配额。
    """
    if not isinstance(data, dict):
        return ""
    direct = data.get("web_content_link")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    links_obj = data.get("links")
    if isinstance(links_obj, dict):
        for key in ("application/octet-stream", "application/octet_stream"):
            blob = links_obj.get(key)
            if isinstance(blob, dict):
                u = blob.get("url")
                if isinstance(u, str) and u.strip():
                    return u.strip()
        for _k, blob in links_obj.items():
            if isinstance(blob, dict):
                u = blob.get("url")
                if isinstance(u, str) and u.strip():
                    return u.strip()
    medias = data.get("medias")
    if isinstance(medias, list):
        for m in medias:
            if not isinstance(m, dict):
                continue
            link = m.get("link")
            if isinstance(link, dict):
                u = link.get("url")
                if isinstance(u, str) and u.strip():
                    return u.strip()
    return ""


def _list_row_trashable_for_cleanup(f: dict) -> bool:
    """清空网盘兜底列表：排除不可进回收站的系统目录（否则会整批任务失败）。"""
    if not isinstance(f, dict):
        return False
    if f.get("writable") is False:
        return False
    name = (f.get("name") or "").strip().lower()
    if "operating system" in name or "操作系统" in name:
        return False
    return True


def _token_exchange_error_should_password_login(exc: httpx.HTTPStatusError) -> bool:
    """
    refresh_token 失效、被其它会话轮换、或 access 已撤销时，用已保存的账号密码重新 signin。
    非此类错误（如 429）不应盲目走密码登录。
    """
    code = exc.response.status_code
    if code == 401:
        return True
    if code != 400:
        return False
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            if body.get("error") == "invalid_grant":
                return True
            err_code = body.get("error_code")
            if err_code == 4126 or str(err_code) == "4126":
                return True
    except Exception:  # noqa: BLE001
        pass
    return "invalid_grant" in (exc.response.text or "").lower()


class PikPakDriver:
    API_BASE = "https://api-drive.mypikpak.com"
    USER_BASE = "https://user.mypikpak.com"
    _ANDROID_CLIENT_ID = "YNxT9w7GMdWvEOKa"
    _ANDROID_CLIENT_SECRET = "dbw2OtmVEeuUvIptb1Coyg"
    _WEB_CLIENT_ID = "YUMx5nI8ZU8Ap8pm"
    _WEB_CLIENT_SECRET = "dbw2OtmVEeuUvIptb1Coyg"

    def __init__(
        self,
        email: str,
        password: str,
        device_id: str,
        refresh_token: Optional[str] = None,
    ):
        self.email = email
        self.password = password
        self.device_id = device_id
        self.access_token: Optional[str] = None
        self.refresh_token = refresh_token
        self._session_captcha_token: Optional[str] = None
        self._client = httpx.AsyncClient(
            timeout=30.0,
            proxy=settings.PIKPAK_PROXY,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _captcha_for_headers(self) -> Optional[str]:
        return self._session_captcha_token or settings.PIKPAK_CAPTCHA_TOKEN

    def _base_headers(self) -> dict[str, str]:
        client_id = self._effective_client_id()
        headers = {
            "User-Agent": f"protocolversion/200 clientid/{client_id}",
            "X-Device-ID": self.device_id,
            "Content-Type": "application/json",
        }
        cap = self._captcha_for_headers()
        if cap:
            headers["X-Captcha-Token"] = cap
        return headers

    def _shield_init_headers(self) -> dict[str, str]:
        """captcha/init 在未拿到 token 前使用浏览器 UA（与 PikPakAPI 一致）。"""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "X-Device-ID": self.device_id,
            "Content-Type": "application/json",
        }

    def _effective_client_id(self) -> str:
        """
        web 签名策略下若仍是默认 Android client_id，则自动切到 Web client_id。
        避免「签名策略=web + client_id=android」导致 invalid captcha_sign。
        """
        profile = cast(SignProfile, settings.PIKPAK_CAPTCHA_SIGN_PROFILE)
        cid = settings.PIKPAK_CLIENT_ID
        if profile == "web" and cid == self._ANDROID_CLIENT_ID:
            return self._WEB_CLIENT_ID
        return cid

    def _effective_client_secret(self) -> str:
        profile = cast(SignProfile, settings.PIKPAK_CAPTCHA_SIGN_PROFILE)
        csec = settings.PIKPAK_CLIENT_SECRET
        if profile == "web" and settings.PIKPAK_CLIENT_ID == self._ANDROID_CLIENT_ID:
            return self._WEB_CLIENT_SECRET
        return csec

    async def _fetch_captcha_token_from_shield(self) -> str:
        signin_path = "/v1/auth/signin"
        action = f"POST:{self.USER_BASE}{signin_path}"
        meta = shield_meta_for_login(
            username=self.email,
            device_id=self.device_id,
            client_id=self._effective_client_id(),
            sign_profile=cast(SignProfile, settings.PIKPAK_CAPTCHA_SIGN_PROFILE),
        )
        payload: dict[str, Any] = {
            "client_id": self._effective_client_id(),
            "action": action,
            "device_id": self.device_id,
            "meta": meta,
        }
        resp = await self._client.post(
            f"{self.USER_BASE}/v1/shield/captcha/init",
            headers=self._shield_init_headers(),
            params={"client_id": self._effective_client_id()},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("url") or data.get("Url")
        token = data.get("captcha_token") or data.get("captchaToken")
        if url and not token:
            raise RuntimeError(
                "PikPak 要求完成人机验证：请在浏览器打开官方登录页完成验证后，"
                "在开发者工具 Network 中查看 signin 请求，将请求体或头中的 captcha_token "
                "填入后台「PIKPAK_CAPTCHA_TOKEN」并保存后再试。"
                f"（若响应含验证链接，可尝试：{url}）"
            )
        if not token:
            raise RuntimeError(
                "PikPak 未返回 captcha_token，无法登录。"
                f" 可配置 PIKPAK_CAPTCHA_TOKEN 后重试。原始响应: {str(data)[:500]}"
            )
        return str(token)

    async def generate_captcha_token(self) -> str:
        """主动向 shield/captcha/init 申请并缓存 captcha_token。"""
        token = await self._fetch_captcha_token_from_shield()
        self._session_captcha_token = token
        return token

    async def login(self) -> dict:
        captcha = settings.PIKPAK_CAPTCHA_TOKEN
        if not captcha:
            captcha = await self._fetch_captcha_token_from_shield()
        self._session_captcha_token = captcha

        resp = await self._client.post(
            f"{self.USER_BASE}/v1/auth/signin",
            headers=self._base_headers(),
            json={
                "client_id": settings.PIKPAK_CLIENT_ID,
                "client_secret": self._effective_client_secret(),
                "username": self.email,
                "password": self.password,
                "captcha_token": captcha,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        return data

    async def refresh(self) -> dict:
        if not self.refresh_token:
            return await self.login()
        try:
            resp = await self._client.post(
                f"{self.USER_BASE}/v1/auth/token",
                headers=self._base_headers(),
                json={
                    "client_id": settings.PIKPAK_CLIENT_ID,
                    "client_secret": self._effective_client_secret(),
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if _token_exchange_error_should_password_login(exc):
                log.warning(
                    "pikpak_refresh_failed_try_password_login http=%s",
                    exc.response.status_code,
                )
                return await self.login()
            raise
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        return data

    @property
    def _auth_headers(self) -> dict[str, str]:
        assert self.access_token
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def _drive_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """
        网盘 API 在 access 过期后常返回 401：清空内存中的 access 后重走 ensure_token
       （优先 refresh，失败则密码登录）。
        """
        for attempt in range(2):
            await self.ensure_token()
            merged = dict(self._auth_headers)
            extra = kwargs.pop("headers", None)
            if isinstance(extra, dict):
                merged.update(extra)
            resp = await self._client.request(method, url, headers=merged, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                log.warning(
                    "pikpak_drive_401_retry_access method=%s path=%s",
                    method,
                    url.replace(self.API_BASE, "")[:96],
                )
                self.access_token = None
                continue
            return resp
        raise RuntimeError("pikpak_drive_request_unreachable")

    async def ensure_token(self) -> None:
        if self.access_token:
            return
        if self.refresh_token:
            await self.refresh()
            return
        await self.login()

    async def get_quota(self) -> dict:
        resp = await self._drive_request("GET", f"{self.API_BASE}/drive/v1/about")
        resp.raise_for_status()
        quota = resp.json().get("quota") or {}
        return {
            "usage": int(quota.get("usage", 0)),
            "limit": int(quota.get("limit", 0)),
        }

    async def get_drive_about(self) -> dict[str, Any]:
        """原始 drive/v1/about 响应，便于同步用量及探测扩展字段。"""
        resp = await self._drive_request("GET", f"{self.API_BASE}/drive/v1/about")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    async def _resolve_cleanup_filters_json(self) -> str:
        filters_json = self._cleanup_list_filters_json()
        probe = await self._drive_request(
            "GET",
            f"{self.API_BASE}/drive/v1/files",
            params={
                "limit": "10",
                "with_audit": "false",
                "filters": filters_json,
            },
        )
        if probe.status_code == 400:
            log.warning(
                "pikpak_list_filters_rejected http=400, fallback without filters (remaining 判定可能偏乐观)"
            )
            return ""
        return filters_json

    @staticmethod
    def _new_file_response_id(data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        f = data.get("file")
        if isinstance(f, dict):
            fid = f.get("id")
            if isinstance(fid, str) and fid:
                return fid
        fid2 = data.get("id")
        if isinstance(fid2, str) and fid2:
            return fid2
        return None

    async def find_pphub_folder_id_at_root(self) -> Optional[str]:
        """在网盘根目录查找名为 PPHUB 的文件夹。"""
        filters_json = await self._resolve_cleanup_filters_json()
        page_token = ""
        for _ in range(24):
            params: dict[str, str] = {
                "limit": "200",
                "with_audit": "false",
            }
            if filters_json:
                params["filters"] = filters_json
            if page_token:
                params["page_token"] = page_token
            resp = await self._drive_request(
                "GET",
                f"{self.API_BASE}/drive/v1/files",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            files = data.get("files") or []
            for f in files:
                if not isinstance(f, dict):
                    continue
                if str(f.get("kind") or "") != "drive#folder":
                    continue
                if (f.get("name") or "").strip() != PIKPAK_HUB_FOLDER_NAME:
                    continue
                fid = f.get("id")
                if isinstance(fid, str) and fid:
                    return fid
            page_token = str(data.get("next_page_token") or "")
            if not page_token:
                break
        return None

    async def create_pphub_folder_at_root(self) -> str:
        """在根目录创建 PPHUB 文件夹（与 rclone RequestNewFile 一致）。"""
        payload: dict[str, Any] = {
            "kind": "drive#folder",
            "name": PIKPAK_HUB_FOLDER_NAME,
            "parent_id": "",
            "folder_type": "NORMAL",
        }
        resp = await self._drive_request(
            "POST",
            f"{self.API_BASE}/drive/v1/files",
            json=payload,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 名称冲突等：尝试重新查找
            if exc.response.status_code in (400, 409):
                found = await self.find_pphub_folder_id_at_root()
                if found:
                    return found
            raise
        data = resp.json()
        fid = self._new_file_response_id(data)
        if not fid:
            raise RuntimeError(f"PikPak 创建文件夹未返回 id：{str(data)[:400]}")
        return fid

    async def ensure_pphub_folder_id(self) -> str:
        """查找或创建根目录 PPHUB 文件夹并返回其 id。"""
        existing = await self.find_pphub_folder_id_at_root()
        if existing:
            return existing
        return await self.create_pphub_folder_at_root()

    async def add_offline_task(self, url: str, folder_id: str = "") -> dict:
        """
        与 rclone RequestNewTask 一致：指定 parent_id（写入 PPHUB）时 **不得** 再发送 folder_type=DOWNLOAD，
        否则 PikPak 仍将离线文件落到默认「云下载 / My Pack」，等于忽略 parent_id。
        """
        payload: dict = {
            "kind": "drive#file",
            "upload_type": "UPLOAD_TYPE_URL",
            "url": {"url": url},
        }
        if folder_id:
            payload["parent_id"] = folder_id
            # 不显式传 folder_type（等价于 rclone 空字符串 omitempty），避免与 DOWNLOAD 冲突
        else:
            payload["folder_type"] = "DOWNLOAD"
        resp = await self._drive_request(
            "POST",
            f"{self.API_BASE}/drive/v1/files",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_task_status(self, task_id: str) -> dict:
        resp = await self._drive_request("GET", f"{self.API_BASE}/drive/v1/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    async def _wait_batch_async_task(
        self,
        task_id: str,
        *,
        max_wait_s: float = 120.0,
        poll_s: float = 1.0,
    ) -> tuple[bool, str]:
        """
        batchTrash / batchDelete 等接口常返回异步 task_id（与 rclone requestBatchAction 一致）。
        若不等待即 empty_trash 或列目录，会出现「回收站已空但根目录文件仍在」的假失败。
        """
        await asyncio.sleep(0.5)
        deadline = time.monotonic() + max_wait_s
        last_detail = ""
        while time.monotonic() < deadline:
            try:
                st = await self.get_task_status(task_id)
            except httpx.HTTPStatusError as exc:
                return False, f"task_poll_http={exc.response.status_code}"
            phase = st.get("phase")
            msg = st.get("message")
            last_detail = ((str(msg) if msg else "") or (str(phase) if phase else ""))[:240]
            p = (str(phase or "")).upper()
            if "COMPLETE" in p:
                return True, last_detail
            if "ERROR" in p or "FAIL" in p:
                return False, last_detail or "phase_error"
            await asyncio.sleep(poll_s)
        return False, "batch_task_timeout"

    async def get_file_dict(self, file_id: str) -> dict[str, Any]:
        """GET /drive/v1/files/{id} 原始 JSON；失败时返回空 dict。"""
        try:
            resp = await self._drive_request(
                "GET",
                f"{self.API_BASE}/drive/v1/files/{file_id}",
                params={"_magic": "2021", "thumbnail_size": "SIZE_LARGE"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    async def list_folder_leaf_files_meta(
        self,
        root_folder_id: str,
        *,
        page_limit: int = 40,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        """
        枚举某文件夹下所有「叶子文件」（不含子文件夹自身 id）。
        用于磁力/种子离线结果为目录时挑选实际媒体文件。
        """
        filters_json = await self._resolve_cleanup_filters_json()
        out: list[dict[str, Any]] = []
        queue: deque[str] = deque([root_folder_id])
        visited: set[str] = set()
        while queue:
            parent_id = queue.popleft()
            if parent_id in visited:
                continue
            visited.add(parent_id)
            page_token = ""
            for _ in range(max(page_limit, 1)):
                params: dict[str, str] = {
                    "limit": str(max(min(page_size, 500), 10)),
                    "with_audit": "false",
                }
                if filters_json:
                    params["filters"] = filters_json
                if page_token:
                    params["page_token"] = page_token
                params["parent_id"] = parent_id
                resp = await self._drive_request(
                    "GET",
                    f"{self.API_BASE}/drive/v1/files",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                files = data.get("files") or []
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    fid = f.get("id")
                    if not isinstance(fid, str) or not fid:
                        continue
                    kind = str(f.get("kind") or "")
                    if kind == "drive#folder":
                        queue.append(fid)
                    else:
                        out.append(
                            {
                                "id": fid,
                                "name": str(f.get("name") or ""),
                                "size": _parse_pikpak_file_size(f.get("size")),
                                "kind": kind,
                            }
                        )
                page_token = str(data.get("next_page_token") or "")
                if not page_token:
                    break
        return out

    async def resolve_magnet_folder_downloads(
        self,
        root_id: str,
        *,
        min_size_bytes: int,
        max_candidates: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        若 root 为文件夹：返回 (主文件 id, 候选列表按体积降序)。
        min_size_bytes<=0：不展开，返回 (root_id, [])。
        候选为空（异常目录）：返回 (root_id, [])。
        """
        if min_size_bytes <= 0:
            return (root_id, [])
        max_candidates = max(1, min(int(max_candidates or 1), 50))
        meta = await self.get_file_dict(root_id)
        if str(meta.get("kind") or "") != "drive#folder":
            return (root_id, [])
        leaves = await self.list_folder_leaf_files_meta(root_id)
        if not leaves:
            return (root_id, [])
        eligible = [x for x in leaves if int(x.get("size") or 0) >= int(min_size_bytes)]
        pool = eligible if eligible else leaves
        pool.sort(key=lambda x: -int(x.get("size") or 0))
        top = pool[:max_candidates]
        primary = str(top[0]["id"])
        return (primary, top)

    async def get_download_url(self, file_id: str) -> str:
        resp = await self._drive_request(
            "GET",
            f"{self.API_BASE}/drive/v1/files/{file_id}",
            params={"_magic": "2021", "thumbnail_size": "SIZE_LARGE"},
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            log.warning(
                "pikpak_file_response_not_json file_id=%s snip=%s",
                file_id,
                (resp.text or "")[:240].replace("\n", " "),
            )
            return ""
        return _pick_download_url_from_file_json(data)

    async def _batch_drive_action_and_wait(self, action: str, file_ids: list[str]) -> tuple[bool, int, str]:
        """action 为 batchTrash 或 batchDelete（与 rclone requestBatchAction 一致）。"""
        resp = await self._drive_request(
            "POST",
            f"{self.API_BASE}/drive/v1/files:{action}",
            json={"ids": file_ids},
        )
        body = ""
        try:
            body = (resp.text or "")[:300].replace("\n", " ")
        except Exception:  # noqa: BLE001
            body = ""
        ok = resp.status_code in (200, 204)
        if ok and resp.status_code != 204:
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, dict):
                tid = data.get("task_id") or data.get("TaskId")
                if isinstance(tid, str) and tid.strip():
                    wait_ok, wait_detail = await self._wait_batch_async_task(tid.strip())
                    ok = wait_ok
                    if not wait_ok and wait_detail:
                        extra = f" | after_wait={wait_detail}"
                        body = (body + extra)[:420]
        return (ok, int(resp.status_code), body)

    async def _batch_trash_and_wait(self, file_ids: list[str]) -> tuple[bool, int, str]:
        return await self._batch_drive_action_and_wait("batchTrash", file_ids)

    async def _batch_delete_and_wait(self, file_ids: list[str]) -> tuple[bool, int, str]:
        return await self._batch_drive_action_and_wait("batchDelete", file_ids)

    async def _delete_verbose_with_per_item_trash_fallback(
        self,
        file_ids: list[str],
        batch_fn: Callable[[list[str]], Awaitable[tuple[bool, int, str]]],
    ) -> tuple[bool, int, str]:
        """batch_fn 为 _batch_trash_and_wait 或 _batch_delete_and_wait。"""
        ok, code, body = await batch_fn(file_ids)
        if ok or len(file_ids) <= 1:
            return (ok, code, body)
        if not _pikpak_reserved_trash_denied_message(body):
            return (ok, code, body)
        all_ok_or_skipped = True
        parts: list[str] = []
        for fid in file_ids:
            one_ok, _one_code, one_body = await batch_fn([fid])
            if one_ok:
                parts.append(f"{fid[:10]}… ok")
                continue
            if _pikpak_reserved_trash_denied_message(one_body):
                parts.append(f"{fid[:10]}… reserved_skip")
                continue
            all_ok_or_skipped = False
            parts.append(one_body[:180])
        summary = "per_item: " + "; ".join(parts)
        return (all_ok_or_skipped, code, (body + " | " + summary)[:480])

    async def delete_files_verbose(self, file_ids: list[str]) -> tuple[bool, int, str]:
        return await self._delete_verbose_with_per_item_trash_fallback(
            file_ids, self._batch_trash_and_wait
        )

    async def delete_files_permanent_verbose(self, file_ids: list[str]) -> tuple[bool, int, str]:
        """彻底删除（不进回收站），用于清盘兜底。"""
        return await self._delete_verbose_with_per_item_trash_fallback(
            file_ids, self._batch_delete_and_wait
        )

    async def delete_files(self, file_ids: list[str]) -> bool:
        ok, _, _ = await self.delete_files_verbose(file_ids)
        return ok

    def _cleanup_list_filters_json(self) -> str:
        """与 rclone listAll 一致：只列未进回收站且 phase 完成的条目，避免「已删仍被列到」误判。"""
        return json.dumps(
            {"phase": {"eq": "PHASE_TYPE_COMPLETE"}, "trashed": {"eq": False}},
            separators=(",", ":"),
        )

    async def _walk_tree_for_cleanup(
        self,
        root_folder_id: Optional[str],
        *,
        page_limit: int = 20,
        page_size: int = 200,
        with_audit: bool = False,
    ) -> list[str]:
        """
        root_folder_id=None：从网盘根递归枚举（旧行为）。
        root_folder_id 非空：仅从该文件夹向下递归，不包含该文件夹自身 id（用于仅清理 PPHUB 内文件）。
        """
        filters_json = await self._resolve_cleanup_filters_json()
        file_ids: list[str] = []
        folder_depth: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque(
            [(root_folder_id, 0)] if root_folder_id else [("", 0)]
        )
        visited_parents: set[str] = set()

        while queue:
            parent_id, depth = queue.popleft()
            marker = parent_id if parent_id else "__root__"
            if marker in visited_parents:
                continue
            visited_parents.add(marker)

            page_token = ""
            for _ in range(max(page_limit, 1)):
                params: dict[str, str] = {
                    "limit": str(max(min(page_size, 500), 10)),
                    "with_audit": "true" if with_audit else "false",
                }
                if filters_json:
                    params["filters"] = filters_json
                if page_token:
                    params["page_token"] = page_token
                if parent_id:
                    params["parent_id"] = parent_id
                resp = await self._drive_request(
                    "GET",
                    f"{self.API_BASE}/drive/v1/files",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                files = data.get("files") or []
                for f in files:
                    if isinstance(f, dict):
                        if not _list_row_trashable_for_cleanup(f):
                            continue
                        fid = f.get("id")
                        if not isinstance(fid, str) or not fid:
                            continue
                        kind = str(f.get("kind") or "")
                        if kind == "drive#folder":
                            folder_depth[fid] = depth + 1
                            queue.append((fid, depth + 1))
                        else:
                            file_ids.append(fid)
                page_token = str(data.get("next_page_token") or "")
                if not page_token:
                    break

        folder_ids = sorted(folder_depth.keys(), key=lambda x: -folder_depth[x])
        ordered = file_ids + folder_ids
        return list(dict.fromkeys(ordered))

    async def list_descendants_under_folder_for_cleanup(
        self,
        hub_folder_id: str,
        *,
        page_limit: int = 20,
        page_size: int = 200,
        with_audit: bool = False,
    ) -> list[str]:
        """列出指定文件夹下的全部后代文件/子文件夹 id（不含文件夹自身），顺序适用于批量删除。"""
        return await self._walk_tree_for_cleanup(
            hub_folder_id,
            page_limit=page_limit,
            page_size=page_size,
            with_audit=with_audit,
        )

    async def list_all_file_ids(
        self,
        *,
        page_limit: int = 20,
        page_size: int = 200,
        with_audit: bool = False,
        for_trash_cleanup: bool = False,
    ) -> list[str]:
        """
        列出当前网盘可见文件 id（用于高风险清盘兜底）。
        说明：仅用于管理员手动清理，不在普通流程中频繁调用。
        for_trash_cleanup=True 时：
        - 使用 filters 仅统计未回收且 phase 完成的条目（与 rclone 一致）；
        - 自根目录起递归进入子文件夹，先收集文件 id、再按目录深度从深到浅收集文件夹 id，
          避免只列根目录导致「云下载」等目录内文件永远删不到。
        """
        if not for_trash_cleanup:
            ids: list[str] = []
            page_token: str = ""
            for _ in range(max(page_limit, 1)):
                params: dict[str, str] = {
                    "limit": str(max(min(page_size, 500), 10)),
                    "with_audit": "true" if with_audit else "false",
                }
                if page_token:
                    params["page_token"] = page_token
                resp = await self._drive_request(
                    "GET",
                    f"{self.API_BASE}/drive/v1/files",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                files = data.get("files") or []
                for f in files:
                    if isinstance(f, dict):
                        fid = f.get("id")
                        if isinstance(fid, str) and fid:
                            ids.append(fid)
                page_token = str(data.get("next_page_token") or "")
                if not page_token:
                    break
            return ids

        return await self._walk_tree_for_cleanup(
            None,
            page_limit=page_limit,
            page_size=page_size,
            with_audit=with_audit,
        )

    async def empty_trash(self) -> bool:
        resp = await self._drive_request(
            "PATCH",
            f"{self.API_BASE}/drive/v1/files/trash:empty",
            json={},
        )
        return resp.status_code == 200
