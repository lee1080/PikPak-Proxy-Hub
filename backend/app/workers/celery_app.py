from celery import Celery

from app.config import settings

celery_app = Celery(
    "pikpak",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.offline_worker", "app.workers.cleanup_worker"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
