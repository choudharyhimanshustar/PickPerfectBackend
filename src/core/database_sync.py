from pymongo import MongoClient
from src.core.config import settings

class MongoDBSync:
    def __init__(self):
        self.client = None
        self.db = None

    def connect(self):
        if self.db is not None:
            return  # ✅ already connected

        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        print("✔️ Celery MongoDB connected:", self.db.name)

mongodb_sync = MongoDBSync()
