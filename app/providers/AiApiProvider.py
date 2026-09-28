from openai import AsyncOpenAI
from ..config.config import settings

client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY
)


async def estimate_dissolved_oxygen(
    temperature,
    turbidity,
    ph,
    ammonia,
):
    prompt = f"""
Estimate the dissolved oxygen level of a fish pond.

Sensor data:

Temperature: {temperature}°C
Turbidity: {turbidity}
pH: {ph}
Ammonia: {ammonia}

Estimate the dissolved oxygen in mg/L.

Return ONLY the estimated number.
Do not provide advice.
Do not explain.
Do not use Markdown.
Do not include units.
"""

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are SmartPond's dissolved oxygen "
                    "estimation model. Estimate dissolved oxygen "
                    "from fish pond sensor measurements."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    return response.choices[0].message.content


async def get_warning_ai_advice(
    temperature,
    turbidity,
    ph,
    ammonia,
    dissolved_oxygen,
    devices,
    question="",
    language="english"
):

    aerator = devices.get("aerator", False)
    waterpump = devices.get("waterpump", False)
    heater = devices.get("heater", False)

    prompt = f"""
Analyze this fish pond water:

Temperature: {temperature}°C
Turbidity: {turbidity} NTU
pH: {ph}
Ammonia: {ammonia} mg/L
Dissolved Oxygen: {dissolved_oxygen} mg/L

Current device status:
Aerator: {"ON" if aerator else "OFF"}
Water Pump: {"ON" if waterpump else "OFF"}
Heater: {"ON" if heater else "OFF"}

Farmer question:
{question if question else "No question. Give advice based on the water condition."}

Answer language:
{language}

Give a short recommendation for the fish farmer.

Use exactly this format:

Water condition: Write one short sentence.
Risk: Write one short sentence.
Action: Write one short sentence.

Rules:
- Answer using the requested language.
- Use simple words that a fish farmer can easily understand.
- Consider the current device status when giving the Action.
- If a device is already ON and should remain ON, tell the farmer to keep it ON.
- If a device is OFF and should be turned ON to address the water condition, recommend turning it ON.
- Only recommend a device action when it is relevant to the water condition.
- Do not recommend unnecessary device changes.
- Do not use Markdown.
- Do not use bullet points.
- Do not add extra explanations.
"""

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are SmartPond AI. "
                    "You provide simple fish pond water quality advice "
                    "based on sensor readings and current device states."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.7
    )

    return response.choices[0].message.content



async def get_after_fix_advice(
    temperature,
    turbidity,
    ph,
    ammonia,
    dissolved_oxygen,
    language="english"
):
    prompt = f"""
The fish pond water conditions have already been corrected and
all important parameters are now within the normal range.

Current conditions:

Temperature: {temperature}°C
Turbidity: {turbidity}
pH: {ph}
Ammonia: {ammonia}
Dissolved Oxygen: {dissolved_oxygen} mg/L

Answer language: {language}

Provide one short, natural sentence giving the fish farmer
appropriate advice AFTER the water conditions have returned to normal.

The response MUST start exactly with:

Message:

After "Message:", write one short sentence about monitoring
and maintaining stable water conditions.

Rules:

- Answer using the requested language.
- Return exactly one sentence after "Message:".
- Start the sentence with a capital letter.
- Use simple words.
- Be direct and practical.
- Recommend monitoring the pond for an appropriate period of time.
- Mention checking the water conditions again if appropriate.
- Do not recommend turning on any device.
- Do not mention Heater, Aerator, or Waterpump.
- Do not describe the previous problem.
- Do not explain how the problem was fixed.
- Do not mention unnecessary sensor values.
- Do not use Markdown.
- Do not use bullet points.
- Do not add extra explanations.
- Do not use any other labels.
- Keep the message short and natural.
"""

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are SmartPond AI. "
                    "The pond has already been fixed and its water "
                    "parameters are back within the normal range. "
                    "Provide exactly one short post-fix monitoring "
                    "message. The response must start with 'Message:'. "
                    "Do not recommend devices or corrective actions. "
                    "Focus only on monitoring and maintaining stable "
                    "pond conditions. Answer in the language requested "
                    "by the user."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    return response.choices[0].message.content.strip()


