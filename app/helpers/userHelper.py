from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import HTTPException


def ensure_collections_ready(
    user_collection,
    pond_collection,
    sensors_collection
) -> None:
    if (
        user_collection is None
        or pond_collection is None
        or sensors_collection is None
    ):
        raise HTTPException(
            status_code=500,
            detail="Database collections are not initialized"
        )


def to_float(
    value: Any,
    fallback: float = 0.0
) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def format_time_label(
    dt: datetime | None
) -> str:
    if dt is None:
        return "--"

    hour = dt.hour

    if hour == 0:
        return "12 AM"

    if hour == 12:
        return "12 PM"

    if hour > 12:
        return f"{hour - 12} PM"

    return f"{hour} AM"


def serialize_created_at(
    ts: Any
) -> str | None:
    if ts is None:
        return None

    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        return ts.isoformat()

    return None


def safe_object_id(
    raw: str
) -> ObjectId:
    try:
        return ObjectId(raw)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id"
        )