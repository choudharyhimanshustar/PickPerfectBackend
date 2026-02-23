import os
from celery import Celery
from celery.signals import worker_process_init
from src.core.database_sync import mongodb_sync

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "music_ai",
    broker=redis_url,
    backend=redis_url,
)

@worker_process_init.connect
def init_worker(**kwargs):
    mongodb_sync.connect()
    
# Auto-discover tasks inside src/app_celery
celery_app.autodiscover_tasks(["src.app_celery"])
