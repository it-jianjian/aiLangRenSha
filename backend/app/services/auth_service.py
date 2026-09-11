"""可选登录 — 密码哈希(pbkdf2-sha256) + HMAC 签名 token，零额外依赖。"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from app.config import get_settings

_ITER = 120_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iter_s))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(user_id: str, ttl_seconds: int = 7 * 24 * 3600) -> str:
    secret = get_settings().auth_secret.encode()
    payload = _b64e(json.dumps({"uid": user_id, "exp": int(time.time()) + ttl_seconds}).encode())
    sig = _b64e(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: Optional[str]) -> Optional[str]:
    """校验 token，返回 user_id；无效/过期返回 None。"""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    secret = get_settings().auth_secret.encode()
    expect = _b64e(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        data = json.loads(_b64d(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data.get("uid")
    except Exception:
        return None
