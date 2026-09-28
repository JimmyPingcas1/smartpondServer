"""
Configurable “wall clock” for DB timestamps and day boundaries.

Default timezone is Asia/Manila (UTC+8). Override with env APP_TIMEZONE
(e.g. APP_TIMEZONE=Asia/Ho_Chi_Minh), using any IANA zone name.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "Asia/Manila"


def get_app_tz() -> ZoneInfo:
    raw = (os.environ.get("APP_TIMEZONE") or _DEFAULT_TZ).strip()
    try:
        return ZoneInfo(raw)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def app_now() -> datetime:
    """Current time in the app timezone (timezone-aware)."""
    return datetime.now(get_app_tz())


def app_start_of_today() -> datetime:
    """Midnight at the start of today in the app timezone."""
    now = app_now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
