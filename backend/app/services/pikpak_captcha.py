"""PikPak shield/captcha 签名：与 Alist 驱动（社区当前常用）对齐，避免 invalid captcha_sign。"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Literal

SignProfile = Literal["android", "web"]

# Alist drivers/pikpak AndroidAlgorithms + AndroidClientVersion
_ANDROID_SALTS = [
    "SOP04dGzk0TNO7t7t9ekDbAmx+eq0OI1ovEx",
    "nVBjhYiND4hZ2NCGyV5beamIr7k6ifAsAbl",
    "Ddjpt5B/Cit6EDq2a6cXgxY9lkEIOw4yC1GDF28KrA",
    "VVCogcmSNIVvgV6U+AochorydiSymi68YVNGiz",
    "u5ujk5sM62gpJOsB/1Gu/zsfgfZO",
    "dXYIiBOAHZgzSruaQ2Nhrqc2im",
    "z5jUTBSIpBN9g4qSJGlidNAutX6",
    "KJE2oveZ34du/g1tiimm",
]
_ANDROID_VERSION = "1.53.2"
_ANDROID_PACKAGE = "com.pikcloud.pikpak"

# Alist WebAlgorithms + WebClientVersion / WebPackageName（需配合 .env 中网页端 PIKPAK_CLIENT_ID）
_WEB_SALTS = [
    "C9qPpZLN8ucRTaTiUMWYS9cQvWOE",
    "+r6CQVxjzJV6LCV",
    "F",
    "pFJRC",
    "9WXYIDGrwTCz2OiVlgZa90qpECPD6olt",
    "/750aCr4lm/Sly/c",
    "RB+DT/gZCrbV",
    "",
    "CyLsf7hdkIRxRm215hl",
    "7xHvLi2tOYP0Y92b",
    "ZGTXXxu8E/MIWaEDB+Sm/",
    "1UI3",
    "E7fP5Pfijd+7K+t6Tg/NhuLq0eEUVChpJSkrKxpO",
    "ihtqpG6FMt65+Xk+tWUH2",
    "NhXXU9rg4XXdzo7u5o",
]
_WEB_VERSION = "2.0.0"
_WEB_PACKAGE = "mypikpak.com"


def captcha_sign(
    device_id: str,
    timestamp_ms: str,
    *,
    client_id: str,
    salts: list[str],
    client_version: str,
    package_name: str,
) -> str:
    sign = f"{client_id}{client_version}{package_name}{device_id}{timestamp_ms}"
    for salt in salts:
        sign = hashlib.md5((sign + salt).encode()).hexdigest()
    return f"1.{sign}"


def shield_meta_for_login(
    *,
    username: str,
    device_id: str,
    client_id: str,
    sign_profile: SignProfile = "android",
) -> dict[str, str]:
    """构造 POST /v1/shield/captcha/init 所需的 meta（含 captcha_sign）。"""
    if sign_profile == "web":
        salts, ver, pkg = _WEB_SALTS, _WEB_VERSION, _WEB_PACKAGE
    else:
        salts, ver, pkg = _ANDROID_SALTS, _ANDROID_VERSION, _ANDROID_PACKAGE

    t = str(int(time.time() * 1000))
    meta: dict[str, str] = {
        "captcha_sign": captcha_sign(
            device_id,
            t,
            client_id=client_id,
            salts=salts,
            client_version=ver,
            package_name=pkg,
        ),
        "client_version": ver,
        "package_name": pkg,
        "timestamp": t,
    }
    if re.match(r"\w+([-+.]\w+)*@\w+([-.]\w+)*\.\w+([-.]\w+)*", username):
        meta["email"] = username
    elif re.match(r"\d{11,18}$", username):
        meta["phone_number"] = username
    else:
        meta["username"] = username
    return meta
