from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PikPakAccountAdminCreate(BaseModel):
    email: str
    password: str
    pool_type: str


class PikPakAccountAdmin(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    pool_type: str
    status: str
    quota_total: int
    quota_used: int
    daily_tasks_left: int


class PikPakAccountListResponse(BaseModel):
    accounts: list[PikPakAccountAdmin]


class RuntimeConfigResponse(BaseModel):
    pikpak_proxy: Optional[str] = None
    pikpak_captcha_token: Optional[str] = None
    pikpak_captcha_sign_profile: str = "android"
    pikpak_fixed_device_id: Optional[str] = None
    # 运行日志落库保留天数；0=仅内存。默认 7，可通过环境变量或本接口运行时修改（重启后以 .env 为准）
    admin_log_retention_days: int = 7


class RuntimeConfigPatch(BaseModel):
    pikpak_proxy: Optional[str] = None
    pikpak_captcha_token: Optional[str] = None
    pikpak_captcha_sign_profile: Optional[str] = None
    pikpak_fixed_device_id: Optional[str] = None
    admin_log_retention_days: Optional[int] = None


class ProxyTestResponse(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    message: str


class PikPakSigninPrecheckRequest(BaseModel):
    email: str
    password: str


class PikPakSigninPrecheckResponse(BaseModel):
    ok: bool
    message: str


class PikPakCaptchaTokenGenerateResponse(BaseModel):
    ok: bool
    message: str
    captcha_token: Optional[str] = None
    device_id: Optional[str] = None


class PikPakAccountCleanupDriveResponse(BaseModel):
    ok: bool
    message: str
    deleted_files: int = 0


class PikPakAccountVerifyItem(BaseModel):
    id: uuid.UUID
    email: str
    pool_type: str
    outcome: str = Field(
        description="ok=令牌有效；relogin_ok=重新登录成功；deactivated=重新登录失败已停用",
    )
    status: str
    message: str


class PikPakAccountVerifyAllResponse(BaseModel):
    ok: bool
    total: int
    ok_count: int
    relogin_ok_count: int
    deactivated_count: int
    results: list[PikPakAccountVerifyItem]


class PikPakAccountDailyTasksPatchResponse(BaseModel):
    ok: bool
    id: uuid.UUID
    email: str
    pool_type: str
    daily_tasks_left: int
    message: str


class PikPakAccountResetDailyLimitsResponse(BaseModel):
    ok: bool
    updated_count: int
    daily_limit: int
    reset_date: str
    message: str


class PikPakAccountSyncFromApiResponse(BaseModel):
    ok: bool
    message: str
    quota_total: int
    quota_used: int
    daily_tasks_left: int
    offline_remaining_from_pikpak: Optional[int] = Field(
        default=None,
        description="仅当 about 响应中解析到离线剩余次数字段时有值；否则剩次数见 daily_tasks_left（免费池为与 PikPak 日额对齐的 Hub 配置）",
    )
    updated_daily_tasks_from_pikpak: bool = False
