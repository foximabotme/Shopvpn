# -*- coding: utf-8 -*-
"""
کلاینت Firebase Cloud Messaging (FCM HTTP v1) برای ارسال نوتیفیکیشن پوش به
اپ اندروید مدیریت.

راه‌اندازی:
    1. یک پروژه‌ی رایگان در https://console.firebase.google.com بساز.
    2. از Project Settings → Service Accounts → Generate new private key یک
       فایل JSON بگیر و مسیرش را در تنظیمات پنل ("firebase_service_account_path")
       یا متغیر محیطی FIREBASE_SERVICE_ACCOUNT_PATH بگذار.
    3. project_id همان فایل به‌عنوان FCM_PROJECT_ID استفاده می‌شود (خودکار
       از خودِ فایل خوانده می‌شود).

اگر فایل سرویس‌اکانت تنظیم نشده باشد، send_to_tokens() چیزی نمی‌فرستد و به‌جای
خطا دادن، سکوت می‌کند (دقیقاً مثل رفتار PUSH_ENABLED برای وب‌پوش) تا نصب‌های
بدون Firebase از کار نیفتند.
"""

import json
import logging
import os
import time

import aiohttp

logger = logging.getLogger(__name__)

_SA_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "")
_cached_access_token = {"token": None, "exp": 0}


def _load_service_account():
    path = _SA_PATH or "firebase-service-account.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("خواندن فایل سرویس‌اکانت Firebase ناموفق بود")
        return None


FCM_ENABLED = _load_service_account() is not None


async def _get_access_token(sa: dict) -> str:
    """توکن OAuth2 کوتاه‌مدت گوگل را با jwt امضاشده با کلید سرویس‌اکانت می‌گیرد.
    کش می‌شود تا هر ارسال، یک درخواست جدید به گوگل نزند."""
    now = time.time()
    if _cached_access_token["token"] and _cached_access_token["exp"] > now + 60:
        return _cached_access_token["token"]

    import jwt  # PyJWT - در requirements.txt اضافه شده

    iat = int(now)
    exp = iat + 3600
    payload = {
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/firebase.messaging",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": iat,
        "exp": exp,
    }
    assertion = jwt.encode(payload, sa["private_key"], algorithm="RS256")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"دریافت access token گوگل ناموفق بود: {data}")
            _cached_access_token["token"] = data["access_token"]
            _cached_access_token["exp"] = iat + int(data.get("expires_in", 3600))
            return data["access_token"]


async def send_to_tokens(tokens: list, title: str, body: str, data: dict = None) -> list:
    """برای هر توکن FCM یک نوتیف می‌فرستد. برمی‌گرداند: لیست توکن‌هایی که
    گوگل گفته دیگر معتبر نیستند (باید از دیتابیس حذف شوند)."""
    sa = _load_service_account()
    if not sa or not tokens:
        return []

    try:
        access_token = await _get_access_token(sa)
    except Exception:
        logger.exception("گرفتن access token برای FCM ناموفق بود")
        return []

    project_id = sa["project_id"]
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }
    invalid_tokens = []

    async with aiohttp.ClientSession() as session:
        for token in tokens:
            message = {
                "message": {
                    "token": token,
                    "notification": {"title": title, "body": body},
                    "data": {k: str(v) for k, v in (data or {}).items()},
                    "android": {"priority": "high"},
                }
            }
            try:
                async with session.post(url, headers=headers, data=json.dumps(message)) as resp:
                    if resp.status == 404 or resp.status == 400:
                        resp_data = await resp.json()
                        status = (resp_data.get("error", {}).get("status", ""))
                        if status in ("NOT_FOUND", "INVALID_ARGUMENT", "UNREGISTERED"):
                            invalid_tokens.append(token)
                    elif resp.status >= 400:
                        logger.warning("ارسال FCM ناموفق (status=%s) برای یک توکن", resp.status)
            except Exception:
                logger.exception("خطا در ارسال پوش FCM")

    return invalid_tokens
