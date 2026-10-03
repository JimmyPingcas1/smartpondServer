# problemIdentifier.py

def identify_problems(
    temperature: float,
    ph: float,
    turbidity: float,
    ammonia: float,
    dissolved_oxygen: float
):
    problems = []

    # Temperature
    if temperature < 25.0:
        problems.append("Water temperature is low")
    elif temperature > 30.0:
        problems.append("Water temperature is high")

    # pH
    if ph < 6.5:
        problems.append("pH level is low")
    elif ph > 7.5:
        problems.append("pH level is high")

    # Turbidity
    # if turbidity < 10.0:
    #     problems.append("Water is too clear")
    elif turbidity > 50.0:
        problems.append("Water is too cloudy")

    # Ammonia
    if ammonia >= 0.2:
        problems.append("Ammonia level is high")

    # Dissolved Oxygen
    if dissolved_oxygen < 5.0:
        problems.append("Oxygen level is low")

    return problems


def identify_problem(
    temperature: float,
    ph: float,
    turbidity: float,
    ammonia: float,
    dissolved_oxygen: float
):
    problems = identify_problems(
        temperature,
        ph,
        turbidity,
        ammonia,
        dissolved_oxygen
    )

    if not problems:
        return "All water conditions are normal."

    if len(problems) == 1:
        return problems[0] + "."

    if len(problems) == 2:
        return f"{problems[0]} and {problems[1]}."

    return ", ".join(problems[:-1]) + f", and {problems[-1]}."
