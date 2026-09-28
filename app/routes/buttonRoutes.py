from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..db import Control_collection, pond_collection
from ..helpers.time_utils import app_now
from ..middleware.authMiddleware import get_current_user_id
from .websockets import control_manager


router = APIRouter()

_DEFAULT_DEVICES = {
    "aerator": False,
    "waterpump": False,
    "heater": False,
}


def _merge_devices(raw) -> dict:
    if not isinstance(raw, dict):
        return dict(_DEFAULT_DEVICES)
    return {
        **_DEFAULT_DEVICES,
        **{key: bool(raw[key]) for key in _DEFAULT_DEVICES if key in raw},
    }


# GET AUTOMATION CONTROL STATUS
@router.get("/api/v1/AutoControl")
async def get_automation_control_status(
    user_id: str = Depends(get_current_user_id),
    pond_id: str = Query(...)
):
    try:
        doc = await Control_collection.find_one({
            "user_id": user_id,
            "pond_id": pond_id
        })

        if not doc:
            return {
                "automation": False,
                "manualMode": True,
                "devices": dict(_DEFAULT_DEVICES)
            }

        automation = bool(doc.get("automation", False))
        devices = _merge_devices(doc.get("devices"))

        return {
            "automation": automation,
            "manualMode": not automation,
            "devices": devices
        }

    except Exception as e:
        print(f"[GET AUTOMATION CONTROL] ERROR: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )




# POST AUTOMATION CONTROL TOGGLE
@router.post("/api/v1/AutoControl")
async def toggle_automation_control(
    user_id: str = Depends(get_current_user_id),
    pond_id: str = Query(...),
    data: dict = Body(...)
):
    try:
        # 1. CHECK POND DEVICE STATUS
        # ---------------------------------------------------------
        pond = await pond_collection.find_one(
            {
                "user_id": user_id,
                "pond_id": pond_id
            },
            {
                "_id": 0,
                "status": 1,
                "device_status": 1
            }
        )

        if not pond:
            raise HTTPException(
                status_code=404,
                detail="Pond not found."
            )

        # Get ESP32/device status
        device_status = str(
            pond.get("device_status", "offline")
        ).strip().lower()

        # If missing, treat it as offline
        if device_status not in ["online", "offline"]:
            device_status = "offline"

        print("==========================================")
        print("AUTOMATION CONTROL")
        print(f"   Pond ID: {pond_id}")
        print(f"   Device Status: {device_status}")

        # 2. BLOCK AUTOMATION IF DEVICE IS OFFLINE
        # ---------------------------------------------------------
        if device_status != "online":
            print("   Automation BLOCKED - Device is OFFLINE")

            raise HTTPException(
                status_code=409,
                detail="Your pond device is currently offline."
            )

        # 3. GET AUTOMATION VALUE
        # ---------------------------------------------------------
        automation = bool(data.get("automation", False))


        # 4. FIND EXISTING CONTROL DOCUMENT
        # ---------------------------------------------------------
        doc = await Control_collection.find_one({
            "user_id": user_id,
            "pond_id": pond_id
        })

        if doc:
            devices = _merge_devices(doc.get("devices"))

            await Control_collection.update_one(
                {
                    "user_id": user_id,
                    "pond_id": pond_id
                },
                {
                    "$set": {
                        "automation": automation,
                        "updated_at": app_now()
                    }
                }
            )

        else:
            devices = dict(_DEFAULT_DEVICES)
            now = app_now()

            await Control_collection.insert_one({
                "pond_id": pond_id,
                "user_id": user_id,
                "created_at": now,
                "updated_at": now,
                "devices": devices,
                "automation": automation
            })

        # 5. LOG
        # ---------------------------------------------------------
        print("   Automation Updated Successfully")
        print(f"   Automation: {automation}")
        print(f"   Manual Mode: {not automation}")
        print(
            f"   Aerator={devices['aerator']}, "
            f"Waterpump={devices['waterpump']}, "
            f"Heater={devices['heater']}"
        )

        # 6. BROADCAST TO FLUTTER / ESP32 CLIENTS
        # ---------------------------------------------------------
        try:
            await control_manager.broadcast(
                {
                    "type": "automation",
                    "automation": automation,
                    "manualMode": not automation,
                    "devices": devices,
                    "status": "updated",
                    "device_status": device_status
                },
                user_id,
                pond_id
            )

            print("   WebSocket Broadcast: SUCCESS")

        except Exception as ws_error:
            print(
                f"   WebSocket Broadcast Error: {str(ws_error)}"
            )

        print("==========================================")

        # 7. RESPONSE
        # ---------------------------------------------------------
        return {
            "status": "success",
            "automation": automation,
            "manualMode": not automation,
            "device_status": device_status,
            "devices": devices
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"[AUTOMATION TOGGLE] ERROR: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# POST MANUAL DEVICE CONTROL
@router.post("/api/v1/ManualDeviceControl")
async def manual_device_control(
    user_id: str = Depends(get_current_user_id),
    pond_id: str = Query(...),
    device: str = Query(...),
    action: str = Query(...)
):
    try:

        # ==========================================
        # CHECK ESP32 DEVICE STATUS
        # ==========================================

        pond = await pond_collection.find_one(
            {
                "user_id": user_id,
                "pond_id": pond_id
            },
            {
                "_id": 0,
                "device_status": 1
            }
        )

        if not pond:
            raise HTTPException(
                status_code=404,
                detail="Pond not found."
            )

        device_status = str(
            pond.get("device_status", "offline")
        ).strip().lower()

        # Missing or invalid status = offline
        if device_status not in ["online", "offline"]:
            device_status = "offline"

        # ==========================================
        # BLOCK MANUAL CONTROL IF DEVICE IS OFFLINE
        # ==========================================

        if device_status != "online":
            print("   Manual Control BLOCKED - Device is OFFLINE")

            raise HTTPException(
                status_code=409,
                detail="Your pond device is currently offline."
            )

        # ==========================================
        # VALIDATE DEVICE AND ACTION
        # ==========================================

        device = device.lower().strip()
        action = action.upper().strip()

        if device not in _DEFAULT_DEVICES:
            raise HTTPException(
                status_code=400,
                detail="Invalid device"
            )

        if action not in ["ON", "OFF"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid action"
            )

        execution_time = app_now()

        # ==========================================
        # FIND CONTROL DOCUMENT
        # ==========================================

        doc = await Control_collection.find_one({
            "user_id": user_id,
            "pond_id": pond_id
        })

        if doc:
            automation = bool(doc.get("automation", False))
            devices = _merge_devices(doc.get("devices"))

            devices[device] = (action == "ON")

            await Control_collection.update_one(
                {
                    "user_id": user_id,
                    "pond_id": pond_id
                },
                {
                    "$set": {
                        "devices": devices,
                        "updated_at": execution_time
                    }
                }
            )

        else:
            automation = False
            devices = dict(_DEFAULT_DEVICES)

            devices[device] = (action == "ON")

            await Control_collection.insert_one({
                "pond_id": pond_id,
                "user_id": user_id,
                "created_at": execution_time,
                "updated_at": execution_time,
                "devices": devices,
                "automation": False
            })

        # ==========================================
        # LOG
        # ==========================================

        print("   Device State Updated Successfully")
        print(f"   Device Status: {device_status}")
        print(f"   Mode: {'AUTOMATION' if automation else 'MANUAL'}")
        print(
            f"   Aerator={devices['aerator']}, "
            f"Waterpump={devices['waterpump']}, "
            f"Heater={devices['heater']}"
        )

        # ==========================================
        # WEBSOCKET BROADCAST
        # ==========================================

        try:
            await control_manager.broadcast(
                {
                    "type": "device_control",
                    "device": device,
                    "action": action,
                    "devices": devices,
                    "automation": automation,
                    "manualMode": not automation,
                    "source": "manual",
                    "status": "updated",
                    "device_status": device_status
                },
                user_id,
                pond_id
            )

            print("   WebSocket Broadcast: SUCCESS")

        except Exception as ws_error:
            print(
                f"   WebSocket Broadcast Error: {str(ws_error)}"
            )

        # ==========================================
        # RESPONSE
        # ==========================================

        return {
            "status": "success",
            "device": device,
            "action": action,
            "devices": devices,
            "automation": automation,
            "manualMode": not automation,
            "device_status": device_status,
            "timestamp": execution_time.isoformat()
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"[MANUAL DEVICE CONTROL] ERROR: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )



# GET DEVICE STATES
@router.get("/api/v1/devices")
async def get_device_states(
    user_id: str = Depends(get_current_user_id),
    pond_id: str = Query(...)
):
    try:
        doc = await Control_collection.find_one({
            "user_id": user_id,
            "pond_id": pond_id
        })

        if not doc:
            return {
                "aerator": _DEFAULT_DEVICES["aerator"],
                "waterpump": _DEFAULT_DEVICES["waterpump"],
                "heater": _DEFAULT_DEVICES["heater"]
            }

        devices = _merge_devices(doc.get("devices"))

        return {
            "aerator": devices["aerator"],
            "waterpump": devices["waterpump"],
            "heater": devices["heater"]
        }

    except Exception as e:
        print(f"[GET DEVICE STATES] ERROR: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

