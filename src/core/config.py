import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # go up to project root
env_path = BASE_DIR / ".env.development"
# print("Loading environment variables from:", env_path)

load_dotenv(env_path)

class Settings:
    MONGO_URI: str = os.getenv("MONGO_URI")
    MONGO_DB: str = os.getenv("MONGO_DB", "pickperfect_db")

settings = Settings()

