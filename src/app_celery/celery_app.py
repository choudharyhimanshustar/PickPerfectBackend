from celery import Celery
from celery.signals import worker_process_init
from src.core.database_sync import mongodb_sync

celery_app = Celery(
    "music_ai",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

@worker_process_init.connect
def init_worker(**kwargs):
    mongodb_sync.connect()
    
# Auto-discover tasks inside src/app_celery
celery_app.autodiscover_tasks(["src.app_celery"])
