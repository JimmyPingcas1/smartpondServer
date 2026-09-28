from datetime import datetime, timezone
from typing import Any
from ..config.security import hash_password, verify_password
from ..middleware.authMiddleware import get_current_user_id
 
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form

from ..db import (
	pond_collection,
	pond_requests_collection,
	sensors_collection,
	user_collection,
)
from ..helpers.userHelper import (
	ensure_collections_ready,
	format_time_label,
	safe_object_id,
	serialize_created_at,
	to_float,
)
from ..model.pondUserAccountModel import (
	ChangePasswordPayload,
	PondDetailsUpdatePayload,
	UserProfileUpdatePayload,
)
from ..time_utils import app_start_of_today

router = APIRouter()


@router.get("/api/v1/user/profile")
async def get_user_profile(user_id: str = Depends(get_current_user_id)):
	ensure_collections_ready(user_collection, pond_collection, sensors_collection)
	user = await user_collection.find_one({"_id": safe_object_id(user_id)})
	if not user:
		raise HTTPException(status_code=404, detail="User not found")
	return {
		"id": str(user.get("_id")),
		"name": (user.get("name") or "").strip(),
		"location": (user.get("location") or "").strip(),
		"profile_image_url": (user.get("profile_image_url") or "").strip(),
		"email": user.get("email", ""),
		"role": user.get("role", "user"),
	}


@router.put("/api/v1/user/profile")
async def update_user_profile(
	payload: UserProfileUpdatePayload,
	user_id: str = Depends(get_current_user_id),
):
	ensure_collections_ready(user_collection, pond_collection, sensors_collection)
	update_fields: dict[str, Any] = {}
	if payload.name is not None:
		name = payload.name.strip()
		if not name:
			raise HTTPException(status_code=400, detail="Name is required")
		update_fields["name"] = name
	if payload.location is not None:
		update_fields["location"] = payload.location.strip()
	if not update_fields:
		raise HTTPException(status_code=400, detail="No profile fields to update")
	update_fields["updated_at"] = datetime.now(timezone.utc)

	result = await user_collection.update_one(
		{"_id": safe_object_id(user_id)},
		{"$set": update_fields},
	)
	if result.matched_count == 0:
		raise HTTPException(status_code=404, detail="User not found")

	user = await user_collection.find_one({"_id": safe_object_id(user_id)})
	return {
		"success": True,
		"user": {
			"id": str(user.get("_id")),
			"name": (user.get("name") or "").strip(),
			"location": (user.get("location") or "").strip(),
			"profile_image_url": (user.get("profile_image_url") or "").strip(),
			"email": user.get("email", ""),
			"role": user.get("role", "user"),
		},
	}


@router.post("/api/v1/user/change-password")
async def change_user_password(
    payload: ChangePasswordPayload,
	user_id: str = Depends(get_current_user_id),
):

    ensure_collections_ready(user_collection, pond_collection, sensors_collection)

    user = await user_collection.find_one(
        {"_id": safe_object_id(user_id)}
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    stored_password = user.get("password") or ""

    # Verify current password against bcrypt hash
    if not verify_password(payload.current_password, stored_password):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect",
        )

    new_pw = payload.new_password.strip()

    if len(new_pw) < 6:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 6 characters",
        )

    # Prevent using the same password again
    if verify_password(new_pw, stored_password):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from the current password",
        )

    # Hash the new password before storing it
    new_hashed_password = hash_password(new_pw)

    result = await user_collection.update_one(
        {"_id": safe_object_id(user_id)},
        {
            "$set": {
                "password": new_hashed_password,
                "updated_at": datetime.now(timezone.utc),
            },
        },
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    return {
        "success": True,
        "message": "Password updated successfully",
    }

@router.post("/api/v1/user/profile-image")
async def upload_user_profile_image(
	user_id: str = Depends(get_current_user_id),
	image: UploadFile | None = File(default=None),
):
	raise HTTPException(
		status_code=403,
		detail="File uploads are currently not allowed",
	)


@router.put("/api/v1/user/pond")
async def update_user_pond_details(
	payload: PondDetailsUpdatePayload,
	user_id: str = Depends(get_current_user_id),
	pond_id: str = Query(...),
):
	ensure_collections_ready(user_collection, pond_collection, sensors_collection)
	name = payload.name.strip()
	location = (payload.location or "").strip()
	if not name:
		raise HTTPException(status_code=400, detail="Pond name is required")

	set_doc: dict[str, Any] = {
		"name": name,
		"pond_name": name,
		"location": location,
		"updated_at": datetime.now(timezone.utc),
	}
	if payload.status is not None:
		st = payload.status.strip().lower()
		if st == "active":
			set_doc["status"] = "Active"
		elif st == "inactive":
			set_doc["status"] = "Inactive"
		else:
			raise HTTPException(status_code=400, detail='status must be "Active" or "Inactive"')

	result = await pond_collection.update_one(
		{"user_id": user_id, "pond_id": pond_id},
		{"$set": set_doc},
	)
	if result.matched_count == 0:
		raise HTTPException(status_code=404, detail="Pond not found for this user")
	doc = await pond_collection.find_one({"user_id": user_id, "pond_id": pond_id})
	out_status = (doc or {}).get("status", set_doc.get("status", "Active"))
	return {
		"success": True,
		"pond": {
			"pond_id": pond_id,
			"name": name,
			"location": location,
			"status": out_status,
		},
	}


@router.post("/api/v1/user/request-pond")
async def request_pond(
	request: Request,
	user_id: str = Depends(get_current_user_id),
	name: str = Form(...),
	location: str = Form(""),
	note: str = Form(""),
	image: UploadFile | None = File(default=None),
):
	"""
	Create a pond addition request for admin approval.
	"""
	ensure_collections_ready(user_collection, pond_collection, sensors_collection)
	if not user_id or not name:
		raise HTTPException(status_code=400, detail="Missing required fields.")

	now = datetime.now(timezone.utc)
	request_image_url = ""

	if image is not None:
		raise HTTPException(
			status_code=403,
			detail="File uploads are currently not allowed",
		)

	user_doc = await user_collection.find_one({"user_id": user_id}, {"name": 1})
	user_name = (user_doc or {}).get("name", "Unknown User")

	request_doc = {
		"user_id": user_id,
		"user_name": user_name,
		"pond_name": name,
		"location": location,
		"note": note,
		"request_image_url": request_image_url,
		"status": "pending",
		"admin_note": "",
		"created_at": now,
		"reviewed_at": None,
	}
	result = await pond_requests_collection.insert_one(request_doc)

	return {"success": True, "request_id": str(result.inserted_id), "status": "pending"}


@router.get("/api/v1/user/pond-requests")
async def get_user_pond_requests(user_id: str = Depends(get_current_user_id)):
	ensure_collections_ready(user_collection, pond_collection, sensors_collection)
	if pond_requests_collection is None:
		raise HTTPException(status_code=500, detail="Pond requests collection is not initialized")

	docs = (
		await pond_requests_collection.find({"user_id": user_id})
		.sort("created_at", -1)
		.to_list(length=300)
	)

	requests = []
	for doc in docs:
		requests.append(
			{
				"id": str(doc.get("_id")),
				"pond_name": doc.get("pond_name", ""),
				"location": doc.get("location", ""),
				"note": doc.get("note", ""),
				"request_image_url": doc.get("request_image_url", ""),
				"status": doc.get("status", "pending"),
				"admin_note": doc.get("admin_note", ""),
				"created_at": doc.get("created_at"),
				"reviewed_at": doc.get("reviewed_at"),
			}
		)

	return {"requests": requests, "count": len(requests)}


@router.get("/api/v1/user/dashboard")
async def get_user_dashboard_data(request: Request, user_id: str = Depends(get_current_user_id), pond_id: str = Query(default=None)):
	ensure_collections_ready(user_collection, pond_collection, sensors_collection)

	# Build pond filter
	pond_query = {}
	if user_id:
		pond_query["user_id"] = user_id
	if pond_id:
		pond_query["pond_id"] = pond_id

	# Fetch all ponds for the user
	ponds = await pond_collection.find(pond_query).to_list(length=2000)
	total_ponds = len(ponds)
	total_users = await user_collection.count_documents({})

	# Get all pond IDs for sensor filtering
	user_pond_ids = [p.get("pond_id") for p in ponds if p.get("pond_id")]

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

	recent_docs = (
		await sensors_collection.find(
			sensor_query,
			{
				"temperature": 1,
				"ph": 1,
				"ammonia": 1,
				"turbidity": 1,
				"predicted_dissolved_oxygen": 1,
				"created_at": 1,
			},
		)
		.sort("created_at", -1)
		.limit(400)
		.to_list(length=400)
	)

	recent_docs.reverse()
	recent_readings = []
	for doc in recent_docs:
		ts = doc.get("created_at")
		recent_readings.append(
			{
				"time": format_time_label(ts),
				"created_at": serialize_created_at(ts),
				"temp": to_float(doc.get("temperature")),
				"ph": to_float(doc.get("ph")),
				"ammonia": to_float(doc.get("ammonia")),
				"turbidity": to_float(doc.get("turbidity")),
				"do": to_float(doc.get("predicted_dissolved_oxygen")),
			}
		)

	# Helper to serialize ObjectId fields in pond dicts
	def serialize_pond(pond):
		pond = dict(pond)
		if '_id' in pond and hasattr(pond['_id'], '__str__'):
			pond['_id'] = str(pond['_id'])
		if 'user_id' in pond and hasattr(pond['user_id'], '__str__'):
			pond['user_id'] = str(pond['user_id'])
		# Add more fields if needed
		return pond

	ponds_serialized = [serialize_pond(p) for p in ponds]

	return {
		"totalPonds": total_ponds,
		"totalUsers": total_users,
		"activePondToday": active_pond_today,
		"recentReadings": recent_readings,
		"ponds": ponds_serialized,
	}


@router.put("/api/v1/user/phone")
async def update_user_phone(
	user_id: str = Depends(get_current_user_id),
    phone_number: str = Query(...),
):
    ensure_collections_ready(
        user_collection,
        pond_collection,
        sensors_collection,
    )

    phone = phone_number.strip()

    if not phone:
        raise HTTPException(
            status_code=400,
            detail="Phone number is required",
        )

    result = await user_collection.update_one(
        {"_id": safe_object_id(user_id)},
        {
            "$set": {
                "phone_number": phone,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    return {
        "success": True,
        "message": "Phone number updated successfully",
        "phone_number": phone,
    }


@router.get("/api/v1/user/contact")
async def get_user_contact(
	user_id: str = Depends(get_current_user_id),
):
    ensure_collections_ready(
        user_collection,
        pond_collection,
        sensors_collection,
    )

    user = await user_collection.find_one(
        {"_id": safe_object_id(user_id)}
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    return {
        "success": True,
        "phone_number": (user.get("phone_number") or "").strip(),
        "email": (user.get("email") or "").strip(),
    }

