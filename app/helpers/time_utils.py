from datetime import datetime
from zoneinfo import ZoneInfo

PH_TIMEZONE = ZoneInfo("Asia/Manila")


def app_now() -> datetime:
    """
    Return the current Philippine date and time.
    """
    return datetime.now(PH_TIMEZONE)