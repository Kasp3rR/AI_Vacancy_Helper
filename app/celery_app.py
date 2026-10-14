from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "ai_vacancy_helper",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.job_time_limit - 30,
    task_time_limit=settings.job_time_limit,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    broker_transport_options={
        "visibility_timeout": 3600,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    result_backend_transport_options={"visibility_timeout": 3600},
    visibility_timeout=3600,
    result_expires=86400,
    task_default_queue="resume_processing",
    beat_schedule={"recover-jobs": {"task": "app.tasks.recover_jobs", "schedule": 30.0}},
)
