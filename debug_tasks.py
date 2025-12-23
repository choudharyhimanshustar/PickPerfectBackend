# debug_tasks.py

from src.app_celery.celery_worker import celery_app

print("Registered Celery Tasks:\n")

for name in sorted(celery_app.tasks.keys()):
    print("-", name)
