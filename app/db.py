from motor.motor_asyncio import AsyncIOMotorClient
from .config.config import settings

MONGO_DETAILS = settings.MONGO_URI

client = None
database = None

sensors_collection = None
Control_collection = None
pond_collection = None
user_collection = None
pond_requests_collection = None
pond_request_activity_collection = None
ai_advice_collection = None
pondAutomation_collection = None
global warning_collection


async def init_db():
    global client, database, sensors_collection
    global Control_collection, pond_collection, user_collection, pond_requests_collection
    global pond_request_activity_collection, ai_advice_collection, pondAutomation_collection
    global warning_collection

    try:
        client = AsyncIOMotorClient(MONGO_DETAILS)

        # Actually test the MongoDB Atlas connection
        await client.admin.command("ping")

        database = client.SMARTPOND

        sensors_collection = database.get_collection("sensorsdata")
        Control_collection = database.get_collection("Button")
        pond_collection = database.get_collection("ponds")
        user_collection = database.get_collection("users")
        pond_requests_collection = database.get_collection("pond_requests")
        pond_request_activity_collection = database.get_collection("pond_request_activity")
        ai_advice_collection = database.get_collection("aiAdvice")
        pondAutomation_collection = database.get_collection("pond_automation")
        warning_collection = database.get_collection("warning_status")

        print("\n===================================")
        print("MongoDB Atlas connection successful!")
        print("===================================")
        print("Database:", database.name)

        print("Collections:")
        print("  sensors_collection:", sensors_collection.name)
        print("  Control_collection:", Control_collection.name)
        print("  pond_collection:", pond_collection.name)
        print("  user_collection:", user_collection.name)
        print("  pond_requests_collection:", pond_requests_collection.name)
        print("  pond_request_activity_collection:", pond_request_activity_collection.name)
        print("  ai_advice_collection:", ai_advice_collection.name)

    except Exception as e:
        print("\n===================================")
        print("MongoDB Atlas connection FAILED!")
        print("===================================")
        print("Error:", e)
        raise RuntimeError("MongoDB initialization failed") from e
 
        