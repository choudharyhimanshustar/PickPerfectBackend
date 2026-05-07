import os
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parents[2]
dotenv_path = BASE_DIR / ".env.development"
print("Loading .env from:", dotenv_path)
if dotenv_path.exists():
    load_dotenv(dotenv_path)

from celery import Celery
from celery.signals import worker_process_init
from src.core.database_sync import mongodb_sync

# celery -A src.app_celery.celery_app:celery_app worker --pool=solo --loglevel=info

# beat scheduler
# celery -A src.app_celery.celery_app:celery_app beat --loglevel=info


redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
print("REDIS_URL:", redis_url)
print("MONGO URI:", os.getenv("MONGO_URI"))
celery_app = Celery(
    "music_ai",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.beat_schedule = {
    "mark-stale-pending-as-failed": {
        "task": "mark_stale_pending_as_failed",
        "schedule": 60.0,  # runs every 60 seconds
    },
    "requeue-from-dlq": {
        "task": "requeue_from_dlq",
        "schedule": 120.0,  # runs every 2 minutes
    },
}
celery_app.conf.timezone = "UTC"
@worker_process_init.connect
def init_worker(**kwargs):
    mongodb_sync.connect()
    
# Auto-discover tasks inside src/app_celery
celery_app.autodiscover_tasks(["src.app_celery"])
