from typing import Dict, Any


# Physical/plausible sensor limits.
# These are NOT the fishpond's safe/danger limits.
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

    if value < SENSOR_LIMITS[sensor_name]["min"]:
        return False

    if value > SENSOR_LIMITS[sensor_name]["max"]:
        return False

    return True


def validate_sensor_data(sensor_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate all sensor readings.

    Returns a result containing:
        valid
        invalid_sensors
        errors
    """

    invalid_sensors = []
    errors = []

    for sensor_name in SENSOR_LIMITS:
        if sensor_name not in sensor_data:
            continue

        value = sensor_data[sensor_name]

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
                    f"the valid sensor range."
                ),
            })

    return {
        "valid": len(invalid_sensors) == 0,
        "invalid_sensors": invalid_sensors,
        "errors": errors,
    }