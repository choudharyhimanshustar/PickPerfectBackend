from src.core.database import mongodb

def get_users_collection():
    return mongodb.db["users"]
