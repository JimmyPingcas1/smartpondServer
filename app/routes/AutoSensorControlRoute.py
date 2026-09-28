# app/routes/AiSensorControlRoute.py
from fastapi import APIRouter, WebSocket, Body, Query, Header
from fastapi.responses import JSONResponse
from fastapi import HTTPException
from datetime import datetime, timezone
 
from ..db import Control_collection, sensors_collection,ai_advice_collection, pond_collection, user_collection
from ..providers.AiApiProvider import estimate_dissolved_oxygen, get_warning_ai_advice, get_after_fix_advice
from ..helpers.problemIdentifier import identify_problem, identify_problems
from ..routes.websockets import control_manager
from ..routes.websockets import sensor_manager
from ..helpers.time_utils import app_now
from zoneinfo import ZoneInfo
from ..helpers.sensorValidator import validate_sensor_data
from ..providers.SmsProvider import send_sensor_out_of_range_sms, send_water_quality_sms, send_device_offline_sms




# ============================================================
# CURRENTLY UNUSED ROUTE FILE
# ============================================================





router = APIRouter()

_DEFAULT_DEVICES = {
    "aerator": False,
    "waterpump": False,
    "heater": False,
}


def _merge_devices(raw) -> dict:
    if not isinstance(raw, dict):
        return dict(_DEFAULT_DEVICES)
    return {**_DEFAULT_DEVICES, **{k: bool(raw[k]) for k in _DEFAULT_DEVICES if k in raw}}



# 1
# sensorData-------------------------------------------------------------
# ESP32 sensor data endpoint
# ----------------------------
@router.post("/api/v1/sensor-auto")
async def process_sensor_data(
    user_id: str = Query(...),
    pond_id: str = Query(...),
    data: dict = Body(...)
):
    try:
        # ============================================================
        # 1. Get the 4 real ESP32 sensor readings
        # ============================================================
        temp = float(data.get("temperature"))
        turbidity = float(data.get("turbidity"))
        ph = float(data.get("ph"))
        ammonia = float(data.get("ammonia"))

        # ============================================================
        # 2. AI estimates the 5th sensor: Dissolved Oxygen
        # ============================================================
        dissolved_oxygen = await estimate_dissolved_oxygen(
            temperature=temp,
            turbidity=turbidity,
            ph=ph,
            ammonia=ammonia
        )

        dissolved_oxygen = float(str(dissolved_oxygen).strip())

        print(
            f"[AI DO] "
            f"Temp={temp}, "
            f"Turbidity={turbidity}, "
            f"pH={ph}, "
            f"Ammonia={ammonia}, "
            f"Estimated DO={dissolved_oxygen}"
        )

        # ============================================================
        # 3. Create ONE Philippine-time timestamp
        # ============================================================
        now = app_now()

        # Philippine time as ISO string
        timestamp = now.isoformat()

        # ============================================================
        # 4. Create the sensor record
        # ============================================================
        sensor_doc = {
            "user_id": user_id,
            "pond_id": pond_id,
            "temperature": temp,
            "turbidity": turbidity,
            "ph": ph,
            "ammonia": ammonia,
            "dissolved_oxygen": dissolved_oxygen,

            # Store Philippine time directly as a string
            "created_at": timestamp,
        }

        # ============================================================
        # 5. Save to MongoDB
        # ============================================================
        await sensors_collection.insert_one(sensor_doc)

        # ============================================================
        # 7. Return sensor data
        # ============================================================
        return JSONResponse(
            content={
                "success": True,
                "user_id": user_id,
                "pond_id": pond_id,
                "temperature": temp,
                "turbidity": turbidity,
                "ph": ph,
                "ammonia": ammonia,
                "dissolved_oxygen": dissolved_oxygen,
                "timestamp": timestamp
            }
        )

    except ValueError as ve:
        return JSONResponse(
            content={
                "success": False,
                "error": "Invalid sensor data format",
                "details": str(ve)
            },
            status_code=400
        )

    except Exception as e:
        print(f"[Sensor AI] Error: {e}")

        return JSONResponse(
            content={
                "success": False,
                "error": str(e)
            },
            status_code=500
        )




# 2
# GET AUTOMATION CONTROL STATUS
@router.get("/api/v1/AutoControl")
async def get_automation_control_status(
    user_id: str = Query(...),
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



# 2
# POST AUTOMATION CONTROL TOGGLE
@router.post("/api/v1/AutoControl")
async def toggle_automation_control(
    user_id: str = Query(...),
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

# 2
# POST MANUAL DEVICE CONTROL
@router.post("/api/v1/ManualDeviceControl")
async def manual_device_control(
    user_id: str = Query(...),
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


# 2
# GET DEVICE STATES
@router.get("/api/v1/devices")
async def get_device_states(
    user_id: str = Query(...),
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

# 1
# POST AUTOMATION DEVICE STATE
# ESP32 -> Server
# Used when ESP32 physically changes a device while AUTOMATION is ON.
@router.post("/api/v1/AutomationDeviceState")
async def automation_device_state(
    user_id: str = Query(...),
    pond_id: str = Query(...),
    device: str = Query(...),
    action: str = Query(...)
):
    try:
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

        doc = await Control_collection.find_one({
            "user_id": user_id,
            "pond_id": pond_id
        })

        if not doc:
            print("   Ignored: No automation document found")
            return {
                "status": "ignored",
                "reason": "No automation configuration found"
            }

        automation = bool(doc.get("automation", False))

        if not automation:
            print("   Ignored: Automation is OFF")
            return {
                "status": "ignored",
                "reason": "Automation is OFF"
            }

        devices = _merge_devices(doc.get("devices"))
        devices[device] = (action == "ON")

        execution_time = app_now()

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

        print("   Physical Device State Updated")
        print("   Automation: ON")
        print(f"   Aerator={devices['aerator']}, Waterpump={devices['waterpump']}, Heater={devices['heater']}")

        try:
            await sensor_manager.broadcast(
                {
                    "type": "device_control",
                    "device": device,
                    "action": action,
                    "devices": devices,
                    "automation": True,
                    "manualMode": False,
                    "source": "esp32_automation",
                    "status": "updated"
                },
                user_id,
                pond_id
            )
            print("   WebSocket Broadcast: SUCCESS")
        except Exception as ws_error:
            print(f"   WebSocket Broadcast Error: {str(ws_error)}")

        return {
            "status": "success",
            "device": device,
            "action": action,
            "devices": devices,
            "automation": True,
            "manualMode": False,
            "source": "esp32_automation",
            "timestamp": execution_time.isoformat()
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"[AUTOMATION DEVICE STATE] ERROR: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )



# 1
# alert
@router.post("/api/v1/warnings")
async def create_warning(
    user_id: str = Query(...),
    pond_id: str = Query(...),
    data: dict = Body(...)
):
    try:
        sensors = data.get("sensors", {})
        esp_status = data.get("status", "warning")

        if not isinstance(sensors, dict):
            return {
                "success": False,
                "message": "Invalid sensors data"
            }

        # ============================================================
        # 1. Get sensor values
        # ============================================================
        try:
            temperature = float(sensors.get("temperature"))
            ph = float(sensors.get("ph"))
            turbidity = float(sensors.get("turbidity"))
            ammonia = float(sensors.get("ammonia"))
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "Sensor values must be valid numbers"
            }

        # ============================================================
        # 2. VALIDATE PHYSICAL SENSOR RANGE
        # ============================================================
        validation = validate_sensor_data({
            "temperature": temperature,
            "ph": ph,
            "turbidity": turbidity,
            "ammonia": ammonia
        })

        if not validation["valid"]:

            print()
            print("=" * 70)
            print("INVALID SENSOR READING")
            print("=" * 70)
            print(f"User ID: {user_id}")
            print(f"Pond ID: {pond_id}")
            print(f"Invalid Sensors: {validation['invalid_sensors']}")
            print(f"Validation Errors: {validation['errors']}")

            # ========================================================
            # 2.1 GET USER PHONE NUMBER
            # ========================================================
            user = await user_collection.find_one(
                {"user_id": user_id},
                {"_id": 0, "name": 1, "phone_number": 1}
            )

            phone_number = None
            user_name = "User"

            if user:
                phone_number = user.get("phone_number")
                user_name = user.get("name", "User")

            # ========================================================
            # 2.2 GET POND NAME
            # ========================================================
            pond = await pond_collection.find_one(
                {"user_id": user_id, "pond_id": pond_id},
                {"_id": 0, "name": 1, "pond_name": 1}
            )

            pond_name = "your pond"

            if pond:
                pond_name = (
                    pond.get("pond_name")
                    or pond.get("name")
                    or "your pond"
                )

            # ========================================================
            # 2.2.1 MARK POND SENSOR STATUS AS FALSE
            # ========================================================
            await pond_collection.update_one(
                {"user_id": user_id, "pond_id": pond_id},
                {
                    "$set": {
                        "sensor_status": False,
                        "updated_at": app_now()
                    }
                }
            )

            print("Pond sensor_status: FALSE")

            # ========================================================
            # 2.3 TURN EVERYTHING OFF
            # ========================================================
            safe_devices = {
                "aerator": False,
                "waterpump": False,
                "heater": False
            }

            # ========================================================
            # 2.4 UPDATE DATABASE
            # ========================================================
            await Control_collection.update_one(
                {"user_id": user_id, "pond_id": pond_id},
                {
                    "$set": {
                        "automation": False,
                        "devices": safe_devices,
                        "updated_at": app_now()
                    }
                },
                upsert=True
            )

            print("Automation: OFF")
            print("Aerator: OFF")
            print("Waterpump: OFF")
            print("Heater: OFF")

            # ========================================================
            # 2.5 BROADCAST AUTOMATION OFF
            # ========================================================
            try:
                await control_manager.broadcast(
                    {
                        "type": "automation",
                        "automation": False,
                        "manualMode": True,
                        "devices": safe_devices,
                        "status": "safety_shutdown",
                        "source": "sensor_validator",
                        "reason": "invalid_sensor_reading",
                        "invalid_sensors": validation["invalid_sensors"],
                        "errors": validation["errors"]
                    },
                    user_id,
                    pond_id
                )
                print("WebSocket Automation OFF: SUCCESS")
            except Exception as ws_error:
                print("WebSocket Automation OFF ERROR:", str(ws_error))

            # ========================================================
            # 2.6 BROADCAST EACH DEVICE OFF
            # ========================================================
            for device in ["aerator", "waterpump", "heater"]:
                try:
                    await control_manager.broadcast(
                        {
                            "type": "device_control",
                            "device": device,
                            "action": "OFF",
                            "devices": safe_devices,
                            "automation": False,
                            "manualMode": True,
                            "source": "sensor_validator",
                            "status": "safety_shutdown",
                            "reason": "invalid_sensor_reading",
                            "invalid_sensors": validation["invalid_sensors"]
                        },
                        user_id,
                        pond_id
                    )
                    print(f"{device.upper()} OFF Broadcast: SUCCESS")
                except Exception as ws_error:
                    print(f"{device.upper()} OFF Broadcast ERROR:", str(ws_error))

            # ========================================================
            # 2.7 SEND SMS
            # ========================================================
            sms_sent = False
            sms_error = None

            if phone_number:
                try:
                    sms_result = send_sensor_out_of_range_sms(
                        phone_number=phone_number,
                        pond_name=pond_name,
                        invalid_sensors=validation["invalid_sensors"]
                    )

                    if isinstance(sms_result, dict):
                        sms_sent = bool(sms_result.get("success", False))
                    else:
                        sms_sent = True

                    print("Out-of-range sensor SMS: SUCCESS")
                except Exception as e:
                    sms_error = str(e)
                    print("Out-of-range sensor SMS ERROR:", sms_error)
            else:
                print("SMS NOT SENT: User has no phone number.")

            print("=" * 70)

            # ========================================================
            # 2.8 STOP HERE
            # ========================================================
            return {
                "success": False,
                "message": (
                    "Invalid sensor reading detected. "
                    "Automation and all devices were turned off."
                ),
                "status": "invalid_sensor",
                "automation": False,
                "devices": safe_devices,
                "invalid_sensors": validation["invalid_sensors"],
                "validation_errors": validation["errors"],
                "sms_sent": sms_sent,
                "sms_error": sms_error
            }

        # ============================================================
        # 2.9 MARK POND SENSOR STATUS AS TRUE (VALID READING)
        # ============================================================
        await pond_collection.update_one(
            {"user_id": user_id, "pond_id": pond_id},
            {
                "$set": {
                    "sensor_status": True,
                    "updated_at": app_now()
                }
            }
        )

        print("Pond sensor_status: TRUE")

        # ============================================================
        # 3. Estimate dissolved oxygen using AI
        # ============================================================
        dissolved_oxygen = await estimate_dissolved_oxygen(
            temperature=temperature,
            ph=ph,
            turbidity=turbidity,
            ammonia=ammonia
        )

        try:
            dissolved_oxygen = float(str(dissolved_oxygen).strip())
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "Invalid dissolved oxygen value"
            }

        # ============================================================
        # 4. Prepare sensor parameters
        # ============================================================
        parameters = {
            "temperature": temperature,
            "ph": ph,
            "turbidity": turbidity,
            "ammonia": ammonia,
            "dissolved_oxygen": dissolved_oxygen
        }

        # ============================================================
        # 5. Identify pond problems
        # ============================================================
        problems = identify_problems(
            temperature=temperature,
            ph=ph,
            turbidity=turbidity,
            ammonia=ammonia,
            dissolved_oxygen=dissolved_oxygen
        )

        problem_message = identify_problem(
            temperature=temperature,
            ph=ph,
            turbidity=turbidity,
            ammonia=ammonia,
            dissolved_oxygen=dissolved_oxygen
        )

        # ============================================================
        # 6. Determine warning/fixed status
        # ============================================================
        if esp_status not in ["warning", "fixed"]:
            status = "warning" if problems else "fixed"
        else:
            status = esp_status

        # ============================================================
        # 6.1 SEND WATER QUALITY SMS
        # ============================================================
        sms_sent = False
        sms_error = None

        try:
            # Get user phone number
            user = await user_collection.find_one(
                {"user_id": user_id},
                {"_id": 0, "name": 1, "phone_number": 1}
            )

            phone_number = None

            if user:
                phone_number = user.get("phone_number")

            # Get pond name
            pond = await pond_collection.find_one(
                {"user_id": user_id, "pond_id": pond_id},
                {"_id": 0, "name": 1, "pond_name": 1}
            )

            pond_name = "your pond"

            if pond:
                pond_name = (
                    pond.get("pond_name")
                    or pond.get("name")
                    or "your pond"
                )

            # Send SMS if phone number exists
            if phone_number:
                sms_result = send_water_quality_sms(
                    phone_number=phone_number,
                    pond_name=pond_name,
                    status=status
                )

                if isinstance(sms_result, dict):
                    sms_sent = bool(sms_result.get("success", False))
                else:
                    sms_sent = True

                if sms_sent:
                    print(f"Water quality {status} SMS: SUCCESS")
                else:
                    print(f"Water quality {status} SMS: FAILED")

            else:
                print(
                    "Water quality SMS NOT SENT: "
                    "User has no phone number."
                )

        except Exception as e:
            sms_error = str(e)
            print("Water quality SMS ERROR:", sms_error)

        # ============================================================
        # 7. Create ONE Philippine-time timestamp
        # ============================================================
        now = app_now()
        timestamp = now.isoformat()

        print("========================================")
        print("[WARNING PH TIME]")
        print("APP NOW:", now)
        print("ISO:", timestamp)
        print("========================================")

        # ============================================================
        # 8. Save sensor record
        # ============================================================
        sensor_doc = {
            "user_id": user_id,
            "pond_id": pond_id,
            "temperature": temperature,
            "ph": ph,
            "turbidity": turbidity,
            "ammonia": ammonia,
            "dissolved_oxygen": dissolved_oxygen,
            "status": status,
            "created_at": timestamp
        }

        sensor_result = await sensors_collection.insert_one(sensor_doc)

        # ============================================================
        # 9. Get current device states
        # ============================================================
        control_doc = await Control_collection.find_one(
            {"user_id": user_id, "pond_id": pond_id},
            {"_id": 0, "devices": 1}
        )

        devices = control_doc.get("devices", {}) if control_doc else {}

        devices = {
            "aerator": bool(devices.get("aerator", False)),
            "waterpump": bool(devices.get("waterpump", False)),
            "heater": bool(devices.get("heater", False))
        }

        # ============================================================
        # 10. Generate AI advice
        # ============================================================
        if status == "warning":
            advice = await get_warning_ai_advice(
                temperature=temperature,
                turbidity=turbidity,
                ph=ph,
                ammonia=ammonia,
                dissolved_oxygen=dissolved_oxygen,
                devices=devices,
                language="english"
            )
        else:
            advice = await get_after_fix_advice(
                temperature=temperature,
                turbidity=turbidity,
                ph=ph,
                ammonia=ammonia,
                dissolved_oxygen=dissolved_oxygen,
                language="english"
            )

        # ============================================================
        # 11. Save AI advice using SAME Philippine timestamp
        # ============================================================
        ai_advice_doc = {
            "user_id": user_id,
            "pond_id": pond_id,
            "status": status,
            "problems": problems,
            "problem_message": problem_message,
            "advice": advice,
            "sensors": parameters,
            "sensor_id": str(sensor_result.inserted_id),
            "created_at": timestamp,
            "read": False
        }

        ai_result = await ai_advice_collection.insert_one(ai_advice_doc)

        # ============================================================
        # 12. Display AI result in CMD
        # ============================================================
        print()
        print("=" * 70)
        print("SMARTPOND AI ADVICE")
        print("=" * 70)

        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            formatted_time = dt.strftime("%B %d, %Y %I:%M %p")
        except (ValueError, TypeError):
            formatted_time = timestamp

        print(f"Time: {formatted_time} (PHT)")
        print()

        if problems:
            if isinstance(problems, list):
                problem_text = ", ".join(str(p) for p in problems)
            else:
                problem_text = str(problems)
            print(f"Problems: {problem_text}")
        else:
            print("Problems: No problems detected.")

        print()
        print(f"AI Advice:")
        print(advice)

        print("=" * 70)
        print()

        # ============================================================
        # 13. Return response
        # ============================================================
        return {
            "success": True,
            "message": "Sensor data and AI advice processed",
            "status": status,
            "parameters": parameters,
            "problems": problems,
            "problem_message": problem_message,
            "advice": advice,
            "sensor_id": str(sensor_result.inserted_id),
            "ai_advice_id": str(ai_result.inserted_id),
            "created_at": timestamp,
            "sms_sent": sms_sent,
            "sms_error": sms_error
        }

    except Exception as e:
        print(f"Warning API error: {e}")

        return {
            "success": False,
            "message": "Failed to process sensor data",
            "error": str(e)
        }




# 3
@router.get("/api/v1/pondProblems")
async def get_pond_problems(
    user_id: str = Query(...)
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




# 3
@router.get("/api/v1/userPondsTrends")
async def get_user_ponds_trends(
    user_id: str = Query(...),
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

# 3
@router.get("/api/v1/userIndividualPondsTrends")
async def get_user_individual_ponds_trends(
    user_id: str = Query(...),
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

# 3
@router.get("/api/v1/latest-do")
async def get_latest_dissolved_oxygen(
    user_id: str = Query(...),
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

 


# 3
@router.get("/api/v1/pondAiAdvice")
async def get_pond_ai_advice(
    user_id: str = Query(...),
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


# 3
@router.get("/api/v1/userPonds")
async def get_control_ponds(
    user_id: str = Query(...)
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



