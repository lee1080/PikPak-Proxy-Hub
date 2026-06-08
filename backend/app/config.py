from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./pikpak_hub.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    PIKPAK_CLIENT_ID: str = "YNxT9w7GMdWvEOKa"
    PIKPAK_CLIENT_SECRET: str = "dbw2OtmVEeuUvIptb1Coyg"
    PIKPAK_PROXY: Optional[str] = None
    PIKPAK_CAPTCHA_TOKEN: Optional[str] = None
    # shield/captcha 签名：android 对应官方 App 凭证；web 需将 PIKPAK_CLIENT_ID 设为 YUMx5nI8ZU8Ap8pm
    PIKPAK_CAPTCHA_SIGN_PROFILE: str = "android"
    # 可选：固定用于预检查/生成 captcha_token 的 device_id，避免上下文漂移导致 token 无效
    PIKPAK_FIXED_DEVICE_ID: Optional[str] = None

    FILE_TTL_HOURS: int = 24
    POLL_INTERVAL_SECONDS: int = 8
    FREE_MAX_FILE_SIZE_BYTES: int = 6_442_450_944
    FREE_DAILY_TASK_LIMIT: int = 3
    # FREE 池「剩次数」每日自动重置的时区（默认北京时间 0 点起算新的一天）
    DAILY_RESET_TIMEZONE: str = "Asia/Shanghai"
    # 开发调试：True 时用 FastAPI 后台任务直连处理，无需 Redis/Celery
    RUN_WORKER_INLINE: bool = True
    AUTO_CREATE_TABLES: bool = True
    CORS_ALLOW_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    CORS_ALLOW_ORIGIN_REGEX: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    DOWNLOAD_SIGNED_LINK_EXPIRE_SECONDS: int = 1800
    # 取链发现网盘已无文件时自动重新离线，同请求内最长等待（秒）
    DOWNLOAD_REOFFLINE_WAIT_MAX_SECONDS: int = 600
    DOWNLOAD_REOFFLINE_POLL_INTERVAL_SECONDS: float = 2.0
    # 取回：PikPak 偶发先 200 但 web_content_link 为空，短重试避免误判为失效
    DOWNLOAD_WEBLINK_RETRY_ATTEMPTS: int = 10
    DOWNLOAD_WEBLINK_RETRY_DELAY_SECONDS: float = 2.0
    # 直链缓存有效期（小时）：复制链接/选集/M3U 优先命中缓存，过期后再回源 PikPak
    DOWNLOAD_LINK_CACHE_TTL_HOURS: int = 72
    # 缓存命中时是否做轻量校验（Range 0-0）。若发现 403/404/410 等则自动回源刷新缓存。
    DOWNLOAD_LINK_CACHE_VALIDATE_ON_HIT: bool = True
    DOWNLOAD_LINK_CACHE_VALIDATE_TIMEOUT_SECONDS: float = 2.0
    # 磁力/种子离线结果常为「文件夹」：取回时若 task.file_id 为文件夹，则枚举其下文件。
    # 0=不展开（与旧行为一致，仍用 task.file_id 取链，文件夹可能无链）；>0 时只把不小于该字节数的文件纳入候选；若一个都没有则退化为整包内最大文件。
    DOWNLOAD_FOLDER_MIN_SIZE_BYTES: int = 100 * 1024 * 1024
    # 文件夹取回时，最多为多少个候选文件解析 PikPak 直链（避免一次取回过慢）
    DOWNLOAD_FOLDER_MAX_LINK_FILES: int = 12
    # 文件夹取回：智能阈值。会基于目录内“疑似视频文件”的大小分布，把阈值限制在 [FLOOR, MIN_SIZE]，
    # 从而在不同剧集体量（90MB/集 vs 1GB/集）下都尽量屏蔽广告/短视频。
    DOWNLOAD_FOLDER_SMART_MIN_SIZE: bool = True
    DOWNLOAD_FOLDER_MIN_SIZE_FLOOR_BYTES: int = 20 * 1024 * 1024
    # 文件夹取回：默认隐藏的关键字（命中则视为广告/无关文件）；仅对文件夹内枚举文件生效
    DOWNLOAD_FOLDER_HIDE_KEYWORDS: str = "更多高清,访问,最新网址,www.,http,tg,群,广告,README,readme,sample,预览,截图"
    DOWNLOAD_FOLDER_VIDEO_EXTS: str = "mp4,mkv,avi,mov,flv,ts,webm,m4v"
    # 刚标记完成的一段时间内若仍无直链，不触发「失效+重离线」（防止重复入库与多扣次数）
    DOWNLOAD_RECENT_COMPLETE_GRACE_MINUTES: int = 15
    # 管理后台「运行日志」环形缓冲条数（内存，用于快速浏览；重启清空）
    ADMIN_LOG_BUFFER_MAX: int = 5000
    # 运行日志落库保留天数；0 表示不写库、不按天清理（仅内存环）
    ADMIN_LOG_RETENTION_DAYS: int = 7

    @field_validator("PIKPAK_PROXY", "PIKPAK_CAPTCHA_TOKEN", "PIKPAK_FIXED_DEVICE_ID", mode="before")
    @classmethod
    def normalize_optional_str(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("PIKPAK_CAPTCHA_SIGN_PROFILE", mode="before")
    @classmethod
    def normalize_captcha_sign_profile(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "android"
        s = str(value).strip().lower()
        if s not in ("android", "web"):
            return "android"
        return s

    @field_validator("ADMIN_LOG_RETENTION_DAYS", mode="before")
    @classmethod
    def clamp_admin_log_retention(cls, value: object) -> int:
        try:
            n = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 7
        return max(0, min(n, 3650))


settings = Settings()
