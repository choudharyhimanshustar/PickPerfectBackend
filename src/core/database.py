from motor.motor_asyncio import AsyncIOMotorClient
from .config import settings

class MongoDB:
    client: AsyncIOMotorClient = None
    db = None

mongodb = MongoDB()

async def connect_to_mongo():
    mongodb.client = AsyncIOMotorClient(settings.MONGO_URI)
    mongodb.db = mongodb.client[settings.MONGO_DB]
    print("ENV MONGO_DB:", settings.MONGO_DB)
    print("Client DB name:", mongodb.db.name)
    print("All collections in this DB:", await mongodb.db.list_collection_names())
    # print("mongo db uri:", settings.MONGO_URI)
    print("✔️ Connected to MongoDB:", settings.MONGO_DB)

async def close_mongo_connection():
    mongodb.client.close()
    print("❌ MongoDB connection closed")
