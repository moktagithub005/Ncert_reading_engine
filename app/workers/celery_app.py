from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "ncert",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_soft_time_limit=max(1, settings.job_timeout_seconds - 30),
    task_time_limit=settings.job_timeout_seconds,
    broker_transport_options={"visibility_timeout": 3600},
    task_max_retries=5,
    task_default_retry_delay=10,
    task_annotations={"*": {"retry_backoff": True, "retry_backoff_max": 300, "retry_jitter": True}},
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)
