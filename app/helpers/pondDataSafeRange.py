# ============================================================
# FISHPOND SAFE RANGES
# ============================================================
# This file defines the safe operating ranges for the
# fishpond's water-quality parameters.
#
# It is used to determine whether valid sensor readings
# indicate a normal pond condition or a water-quality warning.
#
# A value outside the defined safe range does not mean that
# the sensor is faulty. It means the water condition may
# require attention.
#
# Sensor reading validation is handled separately by
# sensorDataValidation.py.
# ============================================================

POND_SAFE_RANGES = {
    "temperature": {
        "min": 25.0,
        "max": 30.0,
    },
    "ph": {
        "min": 6.5,
        "max": 7.5,
    },
    "turbidity": {
        "min": 10.0,
        "max": 50.0,
    },
    "ammonia": {
        "min": 0.0,
        "max": 0.02,
    },
    "dissolved_oxygen": {
        "min": 4.0,
        "max": None,
    },
}


# ============================================================
# CHECK IF ONE VALUE IS WITHIN SAFE RANGE
# ============================================================

def is_within_safe_range(sensor_name: str, value: float) -> bool:

    if sensor_name not in POND_SAFE_RANGES:
        return False

    limits = POND_SAFE_RANGES[sensor_name]

    if limits["min"] is not None and value < limits["min"]:
        return False

    if limits["max"] is not None and value > limits["max"]:
        return False

    return True


# ============================================================
# CHECK ALL POND SENSOR DATA
# ============================================================

def is_pond_data_safe(
    temperature: float,
    ph: float,
    turbidity: float,
    ammonia: float,
    dissolved_oxygen: float
) -> bool:

    return (
        is_within_safe_range("temperature", temperature)
        and is_within_safe_range("ph", ph)
        and is_within_safe_range("turbidity", turbidity)
        and is_within_safe_range("ammonia", ammonia)
        and is_within_safe_range("dissolved_oxygen", dissolved_oxygen)
    )


# ============================================================
# CHECK ONLY THE 4 ACTUAL ESP32 SENSOR DATA
# ============================================================

def is_pond_sensor_data_safe(
    temperature: float,
    ph: float,
    turbidity: float,
    ammonia: float
) -> bool:

    return (
        is_within_safe_range("temperature", temperature)
        and is_within_safe_range("ph", ph)
        and is_within_safe_range("turbidity", turbidity)
        and is_within_safe_range("ammonia", ammonia)
    )
