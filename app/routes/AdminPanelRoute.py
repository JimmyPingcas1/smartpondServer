from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid
import os
import jwt

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel

from ..db import (
    Control_collection,
    pond_collection,
    pond_request_activity_collection,
    pond_requests_collection,
    sensors_collection,
    user_collection,
)
from ..time_utils import app_now, app_start_of_today, get_app_tz
from ..middleware.authMiddleware import require_admin


router = APIRouter(dependencies=[Depends(require_admin)])
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"

# New endpoint: Get true device predictions from DevicePrediction collection
# @router.get("/api/v1/admin/device-predictions")
# async def get_admin_device_predictions(
#     limit: int = Query(default=300, ge=1, le=2000),
#     pond_id: str | None = Query(default=None),
# ):
#     _ensure_collections_ready()

#     query: dict[str, Any] = {}
#     if pond_id:
#         query["pond_id"] = pond_id

#     prediction_docs = (
#         await DevicePredictions_collection.find(
#             query,
#             {
#                 "_id": 1,
#                 "pond_id": 1,
#                 "sensor_id": 1,
#                 "devices": 1,
#                 "detected_issues": 1,
#                 "final_devices": 1,
#                 "danger": 1,
#                 "created_at": 1,
#             },
#         )
#         .sort("created_at", -1)
#         .limit(limit)
#         .to_list(length=limit)
#     )

#     results = []
#     for doc in prediction_docs:
#         ts = doc.get("created_at")
#         if isinstance(ts, datetime):
#             date_str = ts.date().isoformat()
#             time_str = ts.strftime("%H:%M:%S")
#         else:
#             date_str = ""
#             time_str = ""

#         results.append({
#             "id": str(doc.get("_id")),
#             "pondId": doc.get("pond_id", ""),
#             "sensorId": doc.get("sensor_id", ""),
#             "date": date_str,
#             "time": time_str,
#             "final_devices": doc.get("final_devices", {}),
#             "devices": doc.get("devices", {}),
#             "detectedIssues": doc.get("detected_issues", []),
#             "danger": doc.get("danger", False),
#         })

#     return {"devicePredictions": results, "count": len(results)}


def _ensure_collections_ready() -> None:
    if user_collection is None or pond_collection is None or sensors_collection is None:
        raise HTTPException(status_code=500, detail="Database collections are not initialized")


def _serialize_activity_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "request_id": doc.get("request_id", ""),
        "user_id": doc.get("user_id", ""),
        "user_name": doc.get("user_name", "Unknown User"),
        "pond_name": doc.get("pond_name", "Unnamed Pond"),
        "from_status": doc.get("from_status", ""),
        "to_status": doc.get("to_status", ""),
        "admin_note": doc.get("admin_note", ""),
        "actor": doc.get("actor", "admin"),
        "created_at": doc.get("created_at"),
    }


def _extract_role_from_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "viewer"
    token = authorization.replace("Bearer ", "", 1).strip()
    if not token:
        return "viewer"
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return "viewer"
    role = str(payload.get("role", "viewer")).strip().lower()
    return role or "viewer"


def _extract_user_id_from_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "admin_user_id"
    token = authorization.replace("Bearer ", "", 1).strip()
    if not token:
        return "admin_user_id"
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return "admin_user_id"
    user_id = str(payload.get("user_id", "admin_user_id")).strip()
    return user_id or "admin_user_id"


class PondRequestActionPayload(BaseModel):
    status: str
    admin_note: str | None = None


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _to_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "on", "yes"}:
            return True
        if normalized in {"false", "0", "off", "no"}:
            return False
    return None


async def _dashboard_recent_readings(sensor_query: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """Build trend points for all ponds: latest-per-pond average, or hourly averages over 12h."""
    normalized = (mode or "12h").strip().lower()
    if normalized not in ("current", "12h"):
        normalized = "12h"

    if normalized == "current":
        match_q: dict[str, Any] = dict(sensor_query)
        pipeline: list[dict[str, Any]] = [
            {"$match": match_q},
            {"$sort": {"created_at": -1}},
            {
                "$group": {
                    "_id": "$pond_id",
                    "temperature": {"$first": "$temperature"},
                    "ph": {"$first": "$ph"},
                    "ammonia": {"$first": "$ammonia"},
                    "turbidity": {"$first": "$turbidity"},
                    "predicted_dissolved_oxygen": {"$first": "$predicted_dissolved_oxygen"},
                }
            },
        ]
        rows = await sensors_collection.aggregate(pipeline).to_list(length=2000)
        rows = [r for r in rows if r.get("_id")]
        if not rows:
            return []

        def avg_key(key: str) -> float:
            xs = [_to_float(r.get(key)) for r in rows]
            return sum(xs) / len(xs) if xs else 0.0

        return [
            {
                "time": "Current",
                "temp": avg_key("temperature"),
                "ph": avg_key("ph"),
                "ammonia": avg_key("ammonia"),
                "turbidity": avg_key("turbidity"),
                "do": avg_key("predicted_dissolved_oxygen"),
            }
        ]

    cutoff = app_now() - timedelta(hours=12)
    time_q: dict[str, Any] = {**sensor_query, "created_at": {"$gte": cutoff}}
    projection = {
        "temperature": 1,
        "ph": 1,
        "ammonia": 1,
        "turbidity": 1,
        "predicted_dissolved_oxygen": 1,
        "created_at": 1,
    }
    docs = await sensors_collection.find(time_q, projection).sort("created_at", 1).to_list(length=25000)
    if not docs:
        return []

    buckets: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        ts = doc.get("created_at")
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local = ts.astimezone(get_app_tz())
        bucket = local.replace(minute=0, second=0, microsecond=0)
        buckets[bucket].append(doc)

    readings: list[dict[str, Any]] = []
    for bucket_time in sorted(buckets.keys()):
        group = buckets[bucket_time]

        def avg_field(field: str) -> float:
            xs = [_to_float(d.get(field)) for d in group]
            return sum(xs) / len(xs) if xs else 0.0

        readings.append(
            {
                "time": _format_time_label(bucket_time),
                "temp": avg_field("temperature"),
                "ph": avg_field("ph"),
                "ammonia": avg_field("ammonia"),
                "turbidity": avg_field("turbidity"),
                "do": avg_field("predicted_dissolved_oxygen"),
            }
        )
    return readings


def _format_time_label(dt: datetime | None) -> str:
    if dt is None:
        return "--"

    # 12-hour clock, hours 1–12 with spaced uppercase AM/PM (local hour on bucket dt)
    hour = dt.hour
    if hour == 0:
        return "12 AM"
    if hour == 12:
        return "12 PM"
    if hour > 12:
        return f"{hour - 12} PM"
    return f"{hour} AM"


def _infer_prediction(do_value: float, risk_level: str | None) -> str:
    if risk_level == "CRITICAL_LOW":
        return "Danger"
    if risk_level == "LOW":
        return "Warning"
    if risk_level == "OPTIMAL":
        return "Good"
    if risk_level == "HIGH":
        return "Excellent"
    if risk_level == "VERY_HIGH":
        return "Excellent"

    if do_value < 4.5:
        return "Danger"
    if do_value < 6.0:
        return "Warning"
    if do_value < 8.0:
        return "Good"
    return "Excellent"


from fastapi import Request

@router.get("/api/v1/admin/dashboard")
async def get_dashboard_data(
    request: Request,
    user_id: str = Query(default=None),
    pond_id: str = Query(default=None),
    readings_mode: str = Query(default="12h", description="current = latest per pond (averaged); 12h = hourly over last 12h"),
):
    _ensure_collections_ready()

    # Build pond filter
    pond_query = {}
    if user_id:
        pond_query["user_id"] = user_id
    if pond_id:
        pond_query["pond_id"] = pond_id

    # Filter ponds by user/pond
    total_ponds = await pond_collection.count_documents(pond_query)
    total_users = await user_collection.count_documents({})

    # Get only the user's ponds for sensor filtering
    user_pond_ids = []
    if pond_query:
        user_ponds = await pond_collection.find(pond_query, {"pond_id": 1}).to_list(length=2000)
        user_pond_ids = [p.get("pond_id") for p in user_ponds if p.get("pond_id")]

    # Filter sensors by pond(s)
    sensor_query = {}
    if user_pond_ids:
        sensor_query["pond_id"] = {"$in": user_pond_ids}
    elif pond_id:
        sensor_query["pond_id"] = pond_id

    start_of_day = app_start_of_today()
    sensor_query_day = dict(sensor_query)
    sensor_query_day["created_at"] = {"$gte": start_of_day}
    active_pond_ids = await sensors_collection.distinct("pond_id", sensor_query_day)
    active_pond_today = len([pid for pid in active_pond_ids if pid])

    mode_key = (readings_mode or "12h").strip().lower()
    if mode_key not in ("current", "12h"):
        mode_key = "12h"
    recent_readings = await _dashboard_recent_readings(sensor_query, mode_key)

    return {
        "totalPonds": total_ponds,
        "totalUsers": total_users,
        "activePondToday": active_pond_today,
        "recentReadings": recent_readings,
        "readingsMode": mode_key,
    }


@router.get("/api/v1/admin/users")
async def get_admin_users():
    _ensure_collections_ready()

    docs = await user_collection.find({}).sort("created_at", -1).to_list(length=2000)

    users = []
    for doc in docs:
        users.append(
            {
                "id": str(doc.get("_id")),
                "user_id": doc.get("user_id", ""),
                "name": doc.get("name", "Unknown User"),
                "email": doc.get("email", ""),
                "pondId": doc.get("pond_id", ""),
                "status": doc.get("status", "Active"),
            }
        )

    return {"users": users, "count": len(users)}


@router.get("/api/v1/admin/ponds")
async def get_admin_ponds():
    _ensure_collections_ready()

    docs = await pond_collection.find({}).sort("created_at", -1).to_list(length=2000)

    ponds = []
    for doc in docs:
        ponds.append(
            {
                "id": str(doc.get("_id")),
                "pond_id": doc.get("pond_id", ""),
                "name": doc.get("name", "Unnamed Pond"),
                "location": doc.get("location", "Unknown Location"),
                "user_id": doc.get("user_id", ""),
                "user_name": doc.get("user_name", "Unassigned"),
                "devices": int(doc.get("devices_count", 0)),
                "status": doc.get("status", "Active"),
            }
        )

    return {"ponds": ponds, "count": len(ponds)}


# @router.get("/api/v1/admin/records")
# async def get_admin_records(
#     limit: int = Query(default=300, ge=1, le=2000),
#     pond_id: str | None = Query(default=None),
# ):
#     _ensure_collections_ready()

#     query: dict[str, Any] = {}
#     if pond_id:
#         query["pond_id"] = pond_id

#     sensor_docs = (
#         await sensors_collection.find(
#             query,
#             {
#                 "pond_id": 1,
#                 "temperature": 1,
#                 "ph": 1,
#                 "ammonia": 1,
#                 "turbidity": 1,
#                 "predicted_dissolved_oxygen": 1,
#                 "water_quality_prediction": 1,
#                 "do_risk_level": 1,
#                 "aerator_state": 1,
#                 "waterpump_state": 1,
#                 "heater_state": 1,
#                 "device_state_snapshot": 1,
#                 "created_at": 1,
#             },
#         )
#         .sort("created_at", -1)
#         .limit(limit)
#         .to_list(length=limit)
#     )

#     pond_docs = await pond_collection.find({}, {"pond_id": 1, "name": 1}).to_list(length=2000)
#     pond_by_business_id: dict[str, dict[str, Any]] = {}
#     pond_by_object_id: dict[str, dict[str, Any]] = {}
#     for pond_doc in pond_docs:
#         business_id = pond_doc.get("pond_id")
#         if business_id is not None:
#             pond_by_business_id[str(business_id)] = pond_doc
#         pond_doc_id = pond_doc.get("_id")
#         if pond_doc_id is not None:
#             pond_by_object_id[str(pond_doc_id)] = pond_doc

#     prediction_by_sensor_id: dict[str, dict[str, Any]] = {}
#     sensor_ids = [str(doc.get("_id")) for doc in sensor_docs if doc.get("_id") is not None]
#     if DevicePredictions_collection is not None and sensor_ids:
#         prediction_docs = await DevicePredictions_collection.find(
#             {"sensor_id": {"$in": sensor_ids}},
#             {
#                 "sensor_id": 1,
#                 "devices": 1,
#                 "detected_issues": 1,
#             },
#         ).to_list(length=len(sensor_ids) * 2)
#         for prediction_doc in prediction_docs:
#             sensor_id_value = prediction_doc.get("sensor_id")
#             if sensor_id_value is not None:
#                 prediction_by_sensor_id[str(sensor_id_value)] = prediction_doc

#     records = []
#     for doc in sensor_docs:
#         ts = doc.get("created_at")
#         if isinstance(ts, datetime):
#             date_str = ts.date().isoformat()
#             time_str = ts.strftime("%H:%M:%S")
#         else:
#             date_str = ""
#             time_str = ""

#         pond_id_value = doc.get("pond_id", "")
#         pond_key = str(pond_id_value) if pond_id_value is not None else ""
#         pond_doc = pond_by_business_id.get(pond_key) or pond_by_object_id.get(pond_key)
#         pond_business_id = str(pond_doc.get("pond_id")) if pond_doc and pond_doc.get("pond_id") is not None else pond_key
#         pond_name = pond_doc.get("name", "Unknown Pond") if pond_doc else (pond_key or "Unknown Pond")

#         prediction_doc = prediction_by_sensor_id.get(str(doc.get("_id")))
#         has_prediction_doc = prediction_doc is not None
#         devices_payload = prediction_doc.get("devices") if prediction_doc else {}
#         devices_on_raw = devices_payload.get("on", []) if isinstance(devices_payload, dict) else []
#         devices_on = {str(device).upper() for device in devices_on_raw}

#         sensor_snapshot = doc.get("device_state_snapshot")
#         snapshot_aerator = _to_optional_bool(sensor_snapshot.get("aerator")) if isinstance(sensor_snapshot, dict) else None
#         snapshot_waterpump = _to_optional_bool(sensor_snapshot.get("waterpump")) if isinstance(sensor_snapshot, dict) else None
#         snapshot_heater = _to_optional_bool(sensor_snapshot.get("heater")) if isinstance(sensor_snapshot, dict) else None

#         aerator_from_sensor = _to_optional_bool(doc.get("aerator_state"))
#         if aerator_from_sensor is None:
#             aerator_from_sensor = snapshot_aerator

#         waterpump_from_sensor = _to_optional_bool(doc.get("waterpump_state"))
#         if waterpump_from_sensor is None:
#             waterpump_from_sensor = snapshot_waterpump

#         heater_from_sensor = _to_optional_bool(doc.get("heater_state"))
#         if heater_from_sensor is None:
#             heater_from_sensor = snapshot_heater

#         aerator_from_prediction = ("AERATOR" in devices_on) if has_prediction_doc else None
#         waterpump_from_prediction = ("WATER_PUMP" in devices_on) if has_prediction_doc else None
#         heater_from_prediction = ("HEATER" in devices_on) if has_prediction_doc else None

#         aerator_on = aerator_from_sensor if aerator_from_sensor is not None else aerator_from_prediction
#         waterpump_on = waterpump_from_sensor if waterpump_from_sensor is not None else waterpump_from_prediction
#         heater_on = heater_from_sensor if heater_from_sensor is not None else heater_from_prediction

#         detected_issues = prediction_doc.get("detected_issues") if prediction_doc else None
#         if detected_issues is not None and not isinstance(detected_issues, list):
#             detected_issues = []
#         do_value = _to_float(doc.get("predicted_dissolved_oxygen"))
#         prediction = doc.get("water_quality_prediction") or _infer_prediction(do_value, doc.get("do_risk_level"))

#         records.append(
#             {
#                 "id": str(doc.get("_id")),
#                 "date": date_str,
#                 "time": time_str,
#                 "pondId": pond_business_id,
#                 "pondName": pond_name,
#                 "temperature": round(_to_float(doc.get("temperature")), 2),
#                 "ph": round(_to_float(doc.get("ph")), 2),
#                 "ammonia": round(_to_float(doc.get("ammonia")), 3),
#                 "turbidity": round(_to_float(doc.get("turbidity")), 2),
#                 "do": round(do_value, 2),
#                 "predicted_dissolved_oxygen": doc.get("predicted_dissolved_oxygen"),
#                 "prediction": prediction,
#                 "aeratorOn": aerator_on,
#                 "waterPumpOn": waterpump_on,
#                 "heaterOn": heater_on,
#                 "detectedIssues": [str(issue) for issue in detected_issues] if detected_issues is not None else None,
#             }
#         )

#     return {"records": records, "count": len(records)}


@router.get("/api/v1/admin/pond-requests")
async def get_admin_pond_requests(
    status: str = Query(default="pending"),
):
    _ensure_collections_ready()
    if pond_requests_collection is None:
        raise HTTPException(status_code=500, detail="Pond requests collection is not initialized")

    query: dict[str, Any] = {}
    if status != "all":
        query["status"] = status

    docs = (
        await pond_requests_collection.find(query)
        .sort("created_at", -1)
        .to_list(length=500)
    )

    requests: list[dict[str, Any]] = []
    for doc in docs:
        requests.append(
            {
                "id": str(doc.get("_id")),
                "user_id": doc.get("user_id", ""),
                "user_name": doc.get("user_name", "Unknown User"),
                "pond_name": doc.get("pond_name", "Unnamed Pond"),
                "location": doc.get("location", "Unknown Location"),
                "note": doc.get("note", ""),
                "request_image_url": doc.get("request_image_url", ""),
                "status": doc.get("status", "pending"),
                "admin_note": doc.get("admin_note", ""),
                "created_at": doc.get("created_at"),
                "reviewed_at": doc.get("reviewed_at"),
                "status_history": doc.get("status_history", []),
            }
        )

    return {"requests": requests, "count": len(requests)}


@router.patch("/api/v1/admin/pond-requests/{request_id}")
async def update_pond_request_status(
    request_id: str,
    payload: PondRequestActionPayload,
    authorization: str | None = Header(default=None),
):
    _ensure_collections_ready()

    if pond_requests_collection is None:
        raise HTTPException(
            status_code=500,
            detail="Pond requests collection is not initialized"
        )

    actor_role = _extract_role_from_token(authorization)

    if actor_role not in {"admin", "super_admin", "operator"}:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to update request status"
        )

    next_status = payload.status.strip().lower()
    if next_status not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid status. Use approved or rejected.")

    try:
        object_id = ObjectId(request_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid request id") from exc

    request_doc = await pond_requests_collection.find_one({"_id": object_id})
    if request_doc is None:
        raise HTTPException(status_code=404, detail="Pond request not found")

    update_fields: dict[str, Any] = {
        "status": next_status,
        "reviewed_at": datetime.now(timezone.utc),
        "admin_note": (payload.admin_note or "").strip(),
    }
    now = datetime.now(timezone.utc)
    previous_status = str(request_doc.get("status", "pending"))
    history_item = {
        "from_status": previous_status,
        "to_status": next_status,
        "admin_note": update_fields["admin_note"],
        "changed_at": now,
        "actor": "admin",
    }

    if next_status == "approved" and not request_doc.get("resulting_pond_id"):
        if pond_collection is None:
            raise HTTPException(
                status_code=500,
                detail="Pond collection is not initialized"
            )

        if Control_collection is None:
            raise HTTPException(
                status_code=500,
                detail="Control collection is not initialized"
            )

        new_pond_id = str(uuid.uuid4())

        # --------------------------------------------------
        # CREATE POND
        # --------------------------------------------------
        await pond_collection.insert_one(
            {
                "pond_id": new_pond_id,
                "name": request_doc.get("pond_name", "New Pond"),
                "location": request_doc.get("location", ""),
                "user_id": request_doc.get("user_id", ""),
                "user_name": request_doc.get(
                    "user_name",
                    "Unknown User"
                ),

                "status": "Active",

                "created_at": now,
                "updated_at": now,

                "pond_name": request_doc.get(
                    "pond_name",
                    "New Pond"
                ),

                "ai_language": "bisaya",

                # ESP32 starts offline until it connects
                "device_status": "offline",

                # No valid sensor data yet
                "sensor_status": False,
            }
        )

        # --------------------------------------------------
        # CREATE CONTROL / BUTTONS
        # --------------------------------------------------
        await Control_collection.insert_one(
            {
                "user_id": request_doc.get("user_id", ""),
                "pond_id": new_pond_id,

                "created_at": now,

                "devices": {
                    "aerator": False,
                    "waterpump": False,
                    "heater": False,
                },

                "updated_at": now,

                "automation": False,
            }
        )

        # Save the newly created pond ID to the request
        update_fields["resulting_pond_id"] = new_pond_id

    await pond_requests_collection.update_one(
        {"_id": object_id},
        {
            "$set": update_fields,
            "$push": {"status_history": history_item},
        },
    )

    if pond_request_activity_collection is not None:
        await pond_request_activity_collection.insert_one(
            {
                "request_id": request_id,
                "user_id": request_doc.get("user_id", ""),
                "user_name": request_doc.get("user_name", "Unknown User"),
                "pond_name": request_doc.get("pond_name", "Unnamed Pond"),
                "from_status": previous_status,
                "to_status": next_status,
                "admin_note": update_fields["admin_note"],
                "actor": "admin",
                "created_at": now,
            }
        )

    return {"ok": True, "request_id": request_id, "status": next_status}


@router.get("/api/v1/admin/pond-request-activity")
async def get_pond_request_activity(
    limit: int = Query(default=200, ge=1, le=2000),
    request_id: str | None = Query(default=None),
):
    _ensure_collections_ready()
    if pond_request_activity_collection is None:
        return {"activities": [], "count": 0}

    query: dict[str, Any] = {}
    if request_id:
        query["request_id"] = request_id

    docs = (
        await pond_request_activity_collection.find(query)
        .sort("created_at", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "activities": [_serialize_activity_doc(doc) for doc in docs],
        "count": len(docs),
    }


@router.get("/api/v1/admin/notifications")
async def get_admin_notifications(
    state: str = Query(default="unread"),
    limit: int = Query(default=20, ge=1, le=200),
    authorization: str | None = Header(default=None),
):
    _ensure_collections_ready()
    if pond_requests_collection is None:
        return {"notifications": [], "unread_count": 0}

    admin_user_id = _extract_user_id_from_token(authorization)
    docs = (
        await pond_requests_collection.find({})
        .sort("created_at", -1)
        .limit(limit * 3)
        .to_list(length=limit * 3)
    )

    notifications: list[dict[str, Any]] = []
    unread_count = 0
    for doc in docs:
        request_id = str(doc.get("_id"))
        read_by = doc.get("read_by_admin", [])
        is_read = isinstance(read_by, list) and admin_user_id in [str(item) for item in read_by]
        if not is_read:
            unread_count += 1
        if state == "unread" and is_read:
            continue

        notifications.append(
            {
                "id": request_id,
                "request_id": request_id,
                "user_name": doc.get("user_name", "Unknown User"),
                "pond_name": doc.get("pond_name", "Unnamed Pond"),
                "location": doc.get("location", ""),
                "status": doc.get("status", "pending"),
                "is_read": is_read,
                "created_at": doc.get("created_at"),
            }
        )
        if len(notifications) >= limit:
            break

    return {"notifications": notifications, "unread_count": unread_count}


@router.patch("/api/v1/admin/notifications/{request_id}/read")
async def mark_admin_notification_read(
    request_id: str,
    authorization: str | None = Header(default=None),
):
    _ensure_collections_ready()
    if pond_requests_collection is None:
        raise HTTPException(status_code=500, detail="Pond requests collection is not initialized")

    admin_user_id = _extract_user_id_from_token(authorization)
    try:
        object_id = ObjectId(request_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid request id") from exc

    await pond_requests_collection.update_one(
        {"_id": object_id},
        {"$addToSet": {"read_by_admin": admin_user_id}},
    )
    return {"ok": True}


@router.patch("/api/v1/admin/notifications/read-all")
async def mark_all_admin_notifications_read(
    authorization: str | None = Header(default=None),
):
    _ensure_collections_ready()
    if pond_requests_collection is None:
        raise HTTPException(status_code=500, detail="Pond requests collection is not initialized")

    admin_user_id = _extract_user_id_from_token(authorization)
    await pond_requests_collection.update_many(
        {},
        {"$addToSet": {"read_by_admin": admin_user_id}},
    )
    return {"ok": True}
