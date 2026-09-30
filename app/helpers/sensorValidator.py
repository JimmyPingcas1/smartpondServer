from typing import Dict, Any
import math


# ============================================================
# PHYSICAL / PLAUSIBLE SENSOR LIMITS
# ============================================================
# These are NOT the fishpond's safe/danger limits.
#
# They only determine whether a sensor reading is physically
# plausible and usable for further processing.
# ============================================================

SENSOR_LIMITS = {
    "temperature": {
        "min": 0.0,
        "max": 50.0,
    },
    "ph": {
        "min": 0.0,
        "max": 14.0,
    },
    "turbidity": {
        "min": 0.0,
        "max": 500.0,
    },
    "ammonia": {
        "min": 0.0,
        "max": 100.0,
    },
}


# ============================================================
# VALIDATE ONE SENSOR VALUE
# ============================================================

def validate_sensor_value(sensor_name: str, value: Any) -> bool:
    """
    Validate one sensor reading.

    Returns:
        True  -> reading is valid
        False -> reading is invalid
    """

    if sensor_name not in SENSOR_LIMITS:
        return False

    try:
        value = float(value)
    except (TypeError, ValueError):
        return False

    # Reject NaN and Infinity
    if not math.isfinite(value):
        return False

    limits = SENSOR_LIMITS[sensor_name]

    if value < limits["min"]:
        return False

    if value > limits["max"]:
        return False

    return True


# ============================================================
# VALIDATE ALL SENSOR READINGS
# ============================================================

def validate_sensor_data(sensor_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate all required sensor readings.

    Returns:
        valid
        invalid_sensors
        errors
    """

    invalid_sensors = []
    errors = []

    for sensor_name in SENSOR_LIMITS:

        # ----------------------------------------------------
        # Missing sensor reading
        # ----------------------------------------------------
        if sensor_name not in sensor_data:
            invalid_sensors.append(sensor_name)

            limits = SENSOR_LIMITS[sensor_name]

            errors.append({
                "sensor": sensor_name,
                "value": None,
                "min": limits["min"],
                "max": limits["max"],
                "message": (
                    f"{sensor_name} reading is missing."
                ),
            })

            continue

        value = sensor_data[sensor_name]

        # ----------------------------------------------------
        # Invalid sensor reading
        # ----------------------------------------------------
        if not validate_sensor_value(sensor_name, value):

            invalid_sensors.append(sensor_name)

            limits = SENSOR_LIMITS[sensor_name]

            errors.append({
                "sensor": sensor_name,
                "value": value,
                "min": limits["min"],
                "max": limits["max"],
                "message": (
                    f"{sensor_name} reading is outside "
                    f"the valid sensor range or is invalid."
                ),
            })

    return {
        "valid": len(invalid_sensors) == 0,
        "invalid_sensors": invalid_sensors,
        "errors": errors,
    }

