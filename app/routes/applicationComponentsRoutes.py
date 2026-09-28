
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from ..middleware.authMiddleware import get_current_user_id
from ..db import ai_advice_collection, pond_collection, sensors_collection


router = APIRouter()


@router.get("/api/v1/pondProblems")
async def get_pond_problems(
    user_id: str = Depends(get_current_user_id)
):
    try:
        ponds_cursor = pond_collection.find(
            {"user_id": user_id},
            {
                "_id": 1,
                "pond_name": 1,
                "name": 1,
            }
        )

        ponds = []

        async for pond in ponds_cursor:
            pond_id = str(pond["_id"])
            pond_name = pond.get("pond_name") or pond.get("name") or "Unnamed Pond"

            problem_doc = await ai_advice_collection.find_one(
                {
                    "user_id": user_id,
                    "pond_id": pond_id,
                },
                {
                    "_id": 0,
                    "status": 1,
                    "problems": 1,
                    "problem_message": 1,
                    "advice": 1,
                    "created_at": 1,
                },
                sort=[("created_at", -1)]
            )

            if problem_doc:
                problems = problem_doc.get("problems", [])
                if not isinstance(problems, list):
                    problems = []

                ponds.append({
                    "pond_id": pond_id,
                    "pond_name": pond_name,
                    "status": problem_doc.get("status", "fixed"),
                    "problems": problems,
                    "problem_message": problem_doc.get("problem_message", ""),
                    "advice": problem_doc.get("advice", ""),
                    "created_at": problem_doc.get("created_at"),
                })

            else:
                ponds.append({
                    "pond_id": pond_id,
                    "pond_name": pond_name,
                    "status": "fixed",
                    "problems": [],
                    "problem_message": "No issue found",
                    "advice": "",
                    "created_at": None,
                })

        return {
            "success": True,
            "user_id": user_id,
            "ponds": ponds,
        }

    except Exception as e:
        print(f"Pond problems API error: {e}")
        return {
            "success": False,
            "user_id": user_id,
            "ponds": [],
            "message": "Failed to fetch pond problems",
        }





@router.get("/api/v1/userPondsTrends")
async def get_user_ponds_trends(
    user_id: str = Depends(get_current_user_id),
):
    try:
        cursor = sensors_collection.find(
            {"user_id": user_id},
            {
                "_id": 0,
                "user_id": 1,
                "pond_id": 1,
                "temperature": 1,
                "temp_c": 1,
                "ph": 1,
                "turbidity": 1,
                "turbidity_ntu": 1,
                "ammonia": 1,
                "ammonia_scaled": 1,
                "dissolved_oxygen": 1,
                "created_at": 1,
            }
        ).sort("created_at", 1)

        sensor_data = []
        async for doc in cursor:
            sensor_data.append({
                "pond_id": str(doc.get("pond_id", "")),
                "temperature": doc.get("temperature", doc.get("temp_c")),
                "ph": doc.get("ph"),
                "turbidity": doc.get("turbidity", doc.get("turbidity_ntu")),
                "ammonia": doc.get("ammonia", doc.get("ammonia_scaled")),
                "dissolved_oxygen": doc.get("dissolved_oxygen"),
                "timestamp": doc.get("created_at"),
            })

        return {
            "success": True,
            "user_id": user_id,
            "data": sensor_data,
        }

    except Exception as e:
        print(f"User ponds trends API error: {e}")
        return {
            "success": False,
            "message": "Failed to fetch user pond sensor trends",
            "data": [],
        }


@router.get("/api/v1/userIndividualPondsTrends")
async def get_user_individual_ponds_trends(
    user_id: str = Depends(get_current_user_id),
):
    try:
        cursor = sensors_collection.find(
            {"user_id": user_id},
            {
                "_id": 0,
                "user_id": 1,
                "pond_id": 1,
                "temperature": 1,
                "temp_c": 1,
                "ph": 1,
                "turbidity": 1,
                "turbidity_ntu": 1,
                "ammonia": 1,
                "ammonia_scaled": 1,
                "dissolved_oxygen": 1,
                "created_at": 1,
            }
        ).sort("created_at", 1)

        ponds = {}

        async for doc in cursor:
            pond_id = str(doc.get("pond_id", ""))
            if not pond_id:
                continue

            if pond_id not in ponds:
                ponds[pond_id] = {"pond_id": pond_id, "data": []}

            ponds[pond_id]["data"].append({
                "temperature": doc.get("temperature", doc.get("temp_c")),
                "ph": doc.get("ph"),
                "turbidity": doc.get("turbidity", doc.get("turbidity_ntu")),
                "ammonia": doc.get("ammonia", doc.get("ammonia_scaled")),
                "dissolved_oxygen": doc.get("dissolved_oxygen"),
                "timestamp": doc.get("created_at"),
            })

        return {
            "success": True,
            "user_id": user_id,
            "ponds": list(ponds.values()),
        }

    except Exception as e:
        print(f"User individual ponds trends API error: {e}")
        return {
            "success": False,
            "message": "Failed to fetch user pond sensor trends",
            "ponds": [],
        }


@router.get("/api/v1/latest-do")
async def get_latest_dissolved_oxygen(
    user_id: str = Depends(get_current_user_id),
    pond_id: str = Query(...),
):
    try:
        doc = await sensors_collection.find_one(
            {
                "user_id": user_id,
                "pond_id": pond_id,
                "dissolved_oxygen": {"$exists": True, "$ne": None},
            },
            {
                "_id": 0,
                "user_id": 1,
                "pond_id": 1,
                "dissolved_oxygen": 1,
                "created_at": 1,
            },
            sort=[("created_at", -1)],
        )

        if not doc:
            raise HTTPException(
                status_code=404,
                detail="No dissolved oxygen record found for this pond",
            )

        return {
            "success": True,
            "user_id": user_id,
            "pond_id": pond_id,
            "dissolved_oxygen": doc.get("dissolved_oxygen"),
            "timestamp": doc.get("created_at"),
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"Latest DO API error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch latest dissolved oxygen",
        )

 



@router.get("/api/v1/pondAiAdvice")
async def get_pond_ai_advice(
    user_id: str = Depends(get_current_user_id),
    pond_id: str = Query(...)
):
    try:
        cursor = ai_advice_collection.find(
            {
                "user_id": user_id,
                "pond_id": pond_id,
            },
            {
                "_id": 0,
                "user_id": 1,
                "pond_id": 1,
                "status": 1,
                "problems": 1,
                "problem_message": 1,
                "advice": 1,
                "sensors": 1,
                "sensor_id": 1,
                "created_at": 1,
            },
        )

        advice = []

        async for doc in cursor:
            created_at = doc.get("created_at")

            if created_at is not None:
                # MongoDB datetime
                if isinstance(created_at, datetime):
                    # Treat MongoDB naive datetime as UTC
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(
                            tzinfo=timezone.utc
                        )

                    doc["_sort_created_at"] = created_at

                    # Return ISO string
                    doc["created_at"] = created_at.isoformat()

                # String datetime
                elif isinstance(created_at, str):
                    try:
                        parsed = datetime.fromisoformat(
                            created_at.replace("Z", "+00:00")
                        )

                        # If no timezone was stored, treat it as UTC
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(
                                tzinfo=timezone.utc
                            )

                        doc["_sort_created_at"] = parsed
                        doc["created_at"] = parsed.isoformat()

                    except ValueError:
                        doc["_sort_created_at"] = datetime.min.replace(
                            tzinfo=timezone.utc
                        )

                else:
                    doc["_sort_created_at"] = datetime.min.replace(
                        tzinfo=timezone.utc
                    )

            else:
                doc["_sort_created_at"] = datetime.min.replace(
                    tzinfo=timezone.utc
                )

            advice.append(doc)

        # Sort newest -> oldest AFTER normalizing timestamps
        advice.sort(
            key=lambda x: x["_sort_created_at"],
            reverse=True,
        )

        # Remove internal sorting field
        for doc in advice:
            doc.pop("_sort_created_at", None)

        # Keep only latest 10
        advice = advice[:10]

        # print("==========================================")
        # print("POND AI ADVICE API")
        # print("USER ID:", user_id)
        # print("POND ID:", pond_id)
        # print("RECORD COUNT:", len(advice))

        # for index, item in enumerate(advice):
        #     print(
        #         f"{index}: "
        #         f"{item.get('created_at')} | "
        #         f"{item.get('status')} | "
        #         f"{item.get('sensor_id')}"
        #     )

        # if advice:
        #     print("LATEST RECORD:")
        #     print(advice[0])
        # else:
        #     print("NO AI ADVICE FOUND")

        # print("==========================================")

        return {
            "success": True,
            "user_id": user_id,
            "pond_id": pond_id,
            "data": advice,
        }

    except Exception as e:
        print(f"Pond AI advice API error: {e}")

        return {
            "success": False,
            "message": "Failed to fetch pond AI advice",
            "data": [],
        }



@router.get("/api/v1/userPonds")
async def get_control_ponds(
    user_id: str = Depends(get_current_user_id)
):
    try:
        ponds = await pond_collection.find(
            {
                "user_id": user_id
            },
            {
                "_id": 0,
                "pond_id": 1,
                "name": 1,
                "pond_name": 1,
                "location": 1,
                "user_id": 1,
                "user_name": 1,
                "status": 1,
                "device_status": 1,
                "sensor_status": 1,
                "gsm_number": 1,
                "ai_language": 1,
                "created_at": 1,
                "updated_at": 1,
            }
        ).to_list(length=100)

        # Check ESP32/device status
        for pond in ponds:

            device_status = pond.get("device_status")

            # If device_status does not exist or is empty,
            # create it in MongoDB as offline.
            if not device_status:
                pond_id = pond.get("pond_id")

                if pond_id:
                    await pond_collection.update_one(
                        {
                            "user_id": user_id,
                            "pond_id": pond_id
                        },
                        {
                            "$set": {
                                "device_status": "offline"
                            }
                        }
                    )

                device_status = "offline"

            # Normalize the status
            device_status = str(device_status).strip().lower()

            # Send online_status to Flutter
            if device_status == "online":
                pond["online_status"] = "online"
            else:
                pond["online_status"] = "offline"

        return {
            "success": True,
            "count": len(ponds),
            "data": ponds
        }

    except Exception as e:
        print(f"[User Ponds] Error: {e}")

        return {
            "success": False,
            "message": "Failed to fetch pond data",
            "error": str(e)
        }


