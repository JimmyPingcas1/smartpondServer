from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)

from ..db import (
    Control_collection,
    pond_collection,
    user_collection,
)
 
from ..time_utils import app_now
from ..helpers.userHelper import safe_object_id
from ..middleware.authMiddleware import get_user_id_from_token
from ..providers.SmsProvider import send_device_offline_sms
from websockets.exceptions import ConnectionClosedError

router = APIRouter()

DEFAULT_DEVICES = {
    "aerator": False,
    "waterpump": False,
    "heater": False,
}

def merge_devices(raw) -> dict:
    devices = dict(DEFAULT_DEVICES)
    if not isinstance(raw, dict):
        return devices
    for device in DEFAULT_DEVICES:
        if device in raw:
            devices[device] = bool(raw[device])
    return devices

async def user_owns_pond(
    pond_id: str,
    user_id: str,
) -> bool:
    if pond_collection is None:
        return False
    pond = await pond_collection.find_one({
        "_id": pond_id,
        "user_id": user_id,
    })
    if pond:
        return True
    pond = await pond_collection.find_one({
        "pond_id": pond_id,
        "user_id": user_id,
    })
    return pond is not None

async def authenticate_websocket(
    websocket: WebSocket,
):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Authentication token required",
        )
        return None
    try:
        user_id = get_user_id_from_token(token)
    except WebSocketException as e:
        await websocket.close(
            code=e.code,
            reason=e.reason,
        )
        return None
    except Exception:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid authentication token",
        )
        return None
    return str(user_id)

class WebSocketManager:
    def __init__(self):
        self.connections = []

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        pond_id: str,
        client_type: str,
    ):
        # Prevent duplicate connections for the same
        # user + pond + client type.
        old_connections = []

        for connection in self.connections:
            if (
                connection["user_id"] == user_id
                and connection["pond_id"] == pond_id
                and connection["client_type"] == client_type
            ):
                old_connections.append(connection["websocket"])

        # Remove old connection(s) from manager first.
        for old_websocket in old_connections:
            self.disconnect(old_websocket)

            try:
                await old_websocket.close(
                    code=status.WS_1000_NORMAL_CLOSURE,
                    reason="Replaced by newer connection",
                )
            except Exception:
                pass

        await websocket.accept()

        self.connections.append({
            "websocket": websocket,
            "user_id": user_id,
            "pond_id": pond_id,
            "client_type": client_type,
        })

        print(
            f"\nWEBSOCKET CONNECTED"
            f" | User: {user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
            f" | Client: {client_type}"
            f" | Total: {len(self.connections)}"
        )

    def disconnect(
        self,
        websocket: WebSocket,
    ):
        self.connections = [
            connection
            for connection in self.connections
            if connection["websocket"] is not websocket
        ]
        print(
            f"WEBSOCKET DISCONNECTED"
            f" | Total: {len(self.connections)}"
        )

    async def send(
        self,
        websocket: WebSocket,
        message: dict,
    ):
        await websocket.send_json(message)

    async def broadcast(
        self,
        message: dict,
        user_id: str,
        pond_id: str,
        exclude: WebSocket | None = None,
    ):
        targets = []

        for connection in self.connections:
            if connection["user_id"] != user_id:
                continue

            if connection["pond_id"] != pond_id:
                continue

            websocket = connection["websocket"]

            if exclude is not None and websocket is exclude:
                continue

            targets.append(websocket)

        disconnected = []

        for websocket in targets:
            try:
                await websocket.send_json(message)

            except Exception as e:
                print(f"WEBSOCKET SEND ERROR: {e}")
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket)

sensor_manager = WebSocketManager()
control_manager = WebSocketManager()

def create_sensor_message(
    pond_id: str,
    temperature,
    ph,
    turbidity,
    ammonia,
    timestamp=None,
    source="esp32",
):
    return {
        "type": "sensor_data",
        "pond_id": pond_id,
        "temperature": temperature,
        "ph": ph,
        "turbidity": turbidity,
        "ammonia": ammonia,
        "timestamp": timestamp,
        "source": source,
    }


async def mark_esp32_offline(user_id: str, pond_id: str) -> None:
    """
    Transition pond device_status from 'online' -> 'offline'.

    Only fires the offline SMS when an actual ONLINE -> OFFLINE
    transition happened (modified_count == 1), so repeated
    disconnects / duplicate close events won't spam the user.
    """
    if pond_collection is None:
        print(
            f"[ESP32 OFFLINE] pond_collection is None, "
            f"skipping update | User: {user_id[:8]}... | Pond: {pond_id[:8]}..."
        )
        return

    result = await pond_collection.update_one(
        {
            "user_id": user_id,
            "pond_id": pond_id,
            "device_status": "online",
        },
        {
            "$set": {
                "device_status": "offline",
                "updated_at": app_now(),
            }
        },
    )

    if result.modified_count == 1:
        print(
            f"ESP32 OFFLINE"
            f" | User: {user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
        )

        try:
            user = None
            if user_collection is not None:
                try:
                    user = await user_collection.find_one(
                        {"_id": safe_object_id(user_id)},
                        {"_id": 0, "phone_number": 1},
                    )
                except Exception:
                    pass

            if user is None:
                if user_collection is not None:
                    user = await user_collection.find_one(
                        {"user_id": user_id},
                        {"_id": 0, "phone_number": 1},
                    )

            pond = await pond_collection.find_one(
                {"user_id": user_id, "pond_id": pond_id},
                {"_id": 0, "name": 1, "pond_name": 1},
            )

            phone_number = (user or {}).get("phone_number")
            pond_name = (pond or {}).get("pond_name") or (pond or {}).get("name") or pond_id

            if not phone_number:
                print("[SMS] Device offline SMS skipped: user has no phone number")
                return

            sms_result = send_device_offline_sms(phone_number, pond_name)
            if isinstance(sms_result, dict) and not sms_result.get("success", False):
                print(f"[SMS] Device offline SMS failed: {sms_result.get('error', sms_result)}")
            else:
                print("[SMS] Device offline SMS sent")
        except Exception as sms_error:
            print(f"[SMS] Failed to send device offline SMS: {sms_error}")

    else:
        print(
            f"ESP32 disconnected but pond was already offline"
            f" | User: {user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
        )



@router.websocket("/ws/sensor-data/{pond_id}")
async def websocket_sensor_data(
    websocket: WebSocket,
    pond_id: str,
):
    client_type = (
        websocket.query_params
        .get("client", "flutter")
        .strip()
        .lower()
    )

    if client_type not in {"esp32", "flutter"}:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid client type",
        )
        return

    if client_type == "flutter":
        user_id = await authenticate_websocket(websocket)
        if user_id is None:
            return
    else:
        user_id = websocket.query_params.get("user_id")
        if not user_id:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="User ID required for ESP32",
            )
            return
        user_id = str(user_id)

    if pond_collection is None:
        await websocket.close(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Database not initialized",
        )
        return

    if not await user_owns_pond(pond_id, user_id):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Pond not allowed",
        )
        return

    await sensor_manager.connect(
        websocket=websocket,
        user_id=user_id,
        pond_id=pond_id,
        client_type=client_type,
    )

    try:
        await sensor_manager.send(
            websocket,
            {
                "type": "sensor_ws_connected",
                "pond_id": pond_id,
                "client": client_type,
                "status": "connected",
            },
        )

        while True:
            data = await websocket.receive_json()

            if not isinstance(data, dict):
                await sensor_manager.send(
                    websocket,
                    {
                        "type": "error",
                        "message": "Invalid message format",
                    },
                )
                continue

            message_type = str(
                data.get("type", "")
            ).lower()

            if message_type == "sensor_data":
                temperature = data.get("temperature")
                ph = data.get("ph")
                turbidity = data.get("turbidity")
                ammonia = data.get("ammonia")
                timestamp = data.get("timestamp")

                payload = create_sensor_message(
                    pond_id=pond_id,
                    temperature=temperature,
                    ph=ph,
                    turbidity=turbidity,
                    ammonia=ammonia,
                    timestamp=timestamp,
                    source=client_type,
                )

                print(
                    f"\nSENSOR DATA"
                    f" | Pond: {pond_id[:8]}..."
                    f" | User: {user_id[:8]}..."
                    f" | Client: {client_type}"
                    f" | Temp: {temperature}"
                    f" | pH: {ph}"
                    f" | Turbidity: {turbidity}"
                    f" | Ammonia: {ammonia}"
                )

                await sensor_manager.broadcast(
                    message=payload,
                    user_id=user_id,
                    pond_id=pond_id,
                    exclude=websocket,
                )

                continue

            if message_type == "ping":
                await sensor_manager.send(
                    websocket,
                    {
                        "type": "pong",
                    },
                )
                continue

            if message_type == "query":
                await sensor_manager.send(
                    websocket,
                    {
                        "type": "sensor_ws_status",
                        "pond_id": pond_id,
                        "status": "connected",
                    },
                )
                continue

            await sensor_manager.send(
                websocket,
                {
                    "type": "error",
                    "message": "Unknown WebSocket message type",
                },
            )

    except WebSocketDisconnect:
        print(
            f"\nSENSOR WS DISCONNECTED"
            f" | User: {user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
            f" | Client: {client_type}"
        )
        sensor_manager.disconnect(websocket)

    except Exception as e:
        print(
            f"\nSENSOR WS ERROR"
            f" | User: {user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
            f" | Client: {client_type}"
        )
        print(f"Error: {e}")
        sensor_manager.disconnect(websocket)

        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/auto-control/{user_id}/{pond_id}")
async def websocket_auto_control(
    websocket: WebSocket,
    user_id: str,
    pond_id: str,
):
    client_type = (
        websocket.query_params
        .get("client", "flutter")
        .strip()
        .lower()
    )

    if client_type not in {"esp32", "flutter"}:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid client type",
        )
        return

    if client_type == "flutter":
        authenticated_user_id = await authenticate_websocket(websocket)
        if authenticated_user_id is None:
            return
        if authenticated_user_id != str(user_id):
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="User mismatch",
            )
            return
    else:
        esp32_user_id = websocket.query_params.get("user_id")
        if not esp32_user_id:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="User ID required for ESP32",
            )
            return
        if str(esp32_user_id) != str(user_id):
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="User mismatch",
            )
            return
        authenticated_user_id = str(esp32_user_id)

    if pond_collection is None:
        await websocket.close(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Database not initialized",
        )
        return

    if not await user_owns_pond(pond_id, authenticated_user_id):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Pond not allowed",
        )
        return

    await control_manager.connect(
        websocket=websocket,
        user_id=authenticated_user_id,
        pond_id=pond_id,
        client_type=client_type,
    )

    # =========================================================
    # ESP32 CONNECTED → mark pond device_status = online
    # =========================================================
    if client_type == "esp32":
        await pond_collection.update_one(
            {
                "user_id": authenticated_user_id,
                "pond_id": pond_id,
            },
            {
                "$set": {
                    "device_status": "online",
                    "updated_at": app_now(),
                }
            },
        )

        print(
            f"ESP32 ONLINE"
            f" | User: {authenticated_user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
        )

    try:
        doc = await Control_collection.find_one({
            "user_id": authenticated_user_id,
            "pond_id": pond_id,
        })

        if doc:
            automation = bool(doc.get("automation", False))
            devices = merge_devices(doc.get("devices"))
        else:
            automation = False
            devices = dict(DEFAULT_DEVICES)

        await control_manager.send(
            websocket,
            {
                "type": "automation_state",
                "automation": automation,
                "manualMode": not automation,
                "devices": devices,
                "status": "connected",
            },
        )

        while True:
            data = await websocket.receive_json()

            if not isinstance(data, dict):
                await control_manager.send(
                    websocket,
                    {
                        "type": "error",
                        "message": "Invalid message format",
                    },
                )
                continue

            message_type = str(data.get("type", "")).lower()

            if message_type == "query":
                doc = await Control_collection.find_one({
                    "user_id": authenticated_user_id,
                    "pond_id": pond_id,
                })

                if doc:
                    automation = bool(doc.get("automation", False))
                    devices = merge_devices(doc.get("devices"))
                else:
                    automation = False
                    devices = dict(DEFAULT_DEVICES)

                await control_manager.send(
                    websocket,
                    {
                        "type": "automation_state",
                        "automation": automation,
                        "manualMode": not automation,
                        "devices": devices,
                        "requestId": data.get("requestId"),
                    },
                )

                continue

            if message_type == "device_state":
                device = str(data.get("device", "")).lower()
                action = str(data.get("action", "")).upper()

                if device not in DEFAULT_DEVICES:
                    await control_manager.send(
                        websocket,
                        {
                            "type": "error",
                            "message": "Invalid device",
                        },
                    )
                    continue

                if action not in {"ON", "OFF"}:
                    await control_manager.send(
                        websocket,
                        {
                            "type": "error",
                            "message": "Invalid action",
                        },
                    )
                    continue

                doc = await Control_collection.find_one({
                    "user_id": authenticated_user_id,
                    "pond_id": pond_id,
                })

                if doc:
                    automation = bool(doc.get("automation", False))
                    devices = merge_devices(doc.get("devices"))
                else:
                    automation = False
                    devices = dict(DEFAULT_DEVICES)

                devices[device] = (action == "ON")

                execution_time = app_now()

                if doc:
                    await Control_collection.update_one(
                        {
                            "user_id": authenticated_user_id,
                            "pond_id": pond_id,
                        },
                        {
                            "$set": {
                                "devices": devices,
                                "updated_at": execution_time,
                            }
                        },
                    )
                else:
                    await Control_collection.insert_one({
                        "user_id": authenticated_user_id,
                        "pond_id": pond_id,
                        "created_at": execution_time,
                        "updated_at": execution_time,
                        "automation": automation,
                        "devices": devices,
                    })

                payload = {
                    "type": "device_control",
                    "device": device,
                    "action": action,
                    "devices": devices,
                    "automation": automation,
                    "manualMode": not automation,
                    "status": "updated",
                    "source": client_type,
                }

                await control_manager.broadcast(
                    message=payload,
                    user_id=authenticated_user_id,
                    pond_id=pond_id,
                    exclude=websocket,
                )

                continue

            if message_type == "automation_state":
                automation = bool(data.get("automation", False))

                execution_time = app_now()

                doc = await Control_collection.find_one({
                    "user_id": authenticated_user_id,
                    "pond_id": pond_id,
                })

                if doc:
                    devices = merge_devices(doc.get("devices"))

                    await Control_collection.update_one(
                        {
                            "user_id": authenticated_user_id,
                            "pond_id": pond_id,
                        },
                        {
                            "$set": {
                                "automation": automation,
                                "updated_at": execution_time,
                            }
                        },
                    )
                else:
                    devices = dict(DEFAULT_DEVICES)

                    await Control_collection.insert_one({
                        "user_id": authenticated_user_id,
                        "pond_id": pond_id,
                        "created_at": execution_time,
                        "updated_at": execution_time,
                        "automation": automation,
                        "devices": devices,
                    })

                payload = {
                    "type": "automation",
                    "automation": automation,
                    "manualMode": not automation,
                    "devices": devices,
                    "status": "updated",
                    "source": client_type,
                }

                await control_manager.broadcast(
                    message=payload,
                    user_id=authenticated_user_id,
                    pond_id=pond_id,
                    exclude=websocket,
                )

                continue

            if message_type == "ping":
                await control_manager.send(
                    websocket,
                    {
                        "type": "pong",
                    },
                )
                continue

            await control_manager.send(
                websocket,
                {
                    "type": "error",
                    "message": "Unknown WebSocket message type",
                },
            )

    except ConnectionClosedError as e:
        # =========================================================
        # ESP32 CONNECTION LOST / WEBSOCKET TIMEOUT
        # (Azure keepalive ping timeout → clean log instead of traceback)
        # =========================================================
        print(
            f"\nESP32 CONNECTION LOST"
            f" | User: {authenticated_user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
            f" | Client: {client_type}"
        )

        print(f"Reason: {e}")

        if client_type == "esp32":
            await mark_esp32_offline(
                authenticated_user_id,
                pond_id,
            )

        control_manager.disconnect(websocket)

    except WebSocketDisconnect:
        # =========================================================
        # NORMAL / CLEAN DISCONNECT
        # =========================================================
        if client_type == "esp32":
            await mark_esp32_offline(
                authenticated_user_id,
                pond_id,
            )

        print(
            f"\nAUTO CONTROL WS DISCONNECTED"
            f" | User: {authenticated_user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
            f" | Client: {client_type}"
        )

        control_manager.disconnect(websocket)

    except Exception as e:
        # =========================================================
        # UNEXPECTED SERVER ERROR
        # =========================================================
        print(
            f"\nAUTO CONTROL WS ERROR"
            f" | User: {authenticated_user_id[:8]}..."
            f" | Pond: {pond_id[:8]}..."
            f" | Client: {client_type}"
        )

        print(f"Error: {e}")

        if client_type == "esp32":
            await mark_esp32_offline(
                authenticated_user_id,
                pond_id,
            )

        control_manager.disconnect(websocket)

        try:
            await websocket.close()
        except Exception:
            pass 

  
        