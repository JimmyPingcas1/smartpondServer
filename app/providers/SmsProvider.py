import requests

from ..config.config import settings


PHILSMS_URL = "https://dashboard.philsms.com/api/v3/sms/send"


def send_sms(phone_number: str, message: str):
    try:
        if not settings.PHILSMS_TOKEN:
            return {
                "success": False,
                "error": "PhilSMS token missing"
            }

        payload = {
            "recipient": phone_number.replace("+", ""),
            "sender_id": settings.PHILSMS_SENDER_ID,
            "type": "plain",
            "message": message
        }

        headers = {
            "Authorization": f"Bearer {settings.PHILSMS_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        print("PAYLOAD:", payload)

        response = requests.post(
            PHILSMS_URL,
            json=payload,
            headers=headers,
            timeout=10
        )

        print("STATUS:", response.status_code)
        print("RESPONSE:", response.text)

        try:
            result = response.json()

        except Exception:
            result = {
                "raw_response": response.text
            }

        if response.status_code == 200:
            return {
                "success": True,
                "data": result
            }

        return {
            "success": False,
            "error": {
                "status_code": response.status_code,
                "response": result
            }
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ==========================================================
# 1. DEVICE OFFLINE / POWER LOSS / SENSOR CONNECTION LOSS
# ==========================================================

def send_device_offline_sms(
    phone_number: str,
    pond_name: str
):
    message = (
        f"SMARTPOND ALERT: The device for {pond_name} "
        f"is currently offline. Please check the device, "
        f"power connection, sensors, and network connection. "
    )

    return send_sms(
        phone_number,
        message
    )


# ==========================================================
# 2. WATER QUALITY PROBLEM
# ==========================================================

def send_water_quality_sms(
    phone_number: str,
    pond_name: str,
    status: str,
):
    if status == "warning":
        message = (
            f"SMARTPOND ALERT: A water quality problem was detected "
            f"in {pond_name}. Please open the SMARTPOND app "
            f"and check the pond."
        )

    elif status == "fixed":
        message = (
            f"SMARTPOND UPDATE: The water quality problem in "
            f"{pond_name} has been fixed. Please open the "
            f"SMARTPOND app to check the latest pond status."
        )

    else:
        return {
            "success": False,
            "message": "Invalid water quality status."
        }

    return send_sms(
        phone_number,
        message
    )


def send_sensor_out_of_range_sms(
    phone_number: str,
    pond_name: str,
    invalid_sensors: list,
):
    sensor_names = ", ".join(
        str(sensor).replace("_", " ").title()
        for sensor in invalid_sensors
    )

    message = (
        f"SMARTPOND ALERT: An invalid sensor reading was detected "
        f"in {pond_name}. Affected sensor(s): {sensor_names}. "
        f"Automation and pond devices were turned OFF for safety. "
        f"Please open the SMARTPOND app and check the sensors."
    )

    return send_sms(
        phone_number,
        message
    )
