from celery import Celery
from backend.config import settings

celery_app = Celery(
    "rag_constructor",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.tasks.ingestion_tasks", "backend.tasks.batch_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={
        "visibility_timeout": settings.BATCH_QUESTION_TIMEOUT_SECONDS + 120,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    },
    result_backend_transport_options={
        "visibility_timeout": settings.BATCH_QUESTION_TIMEOUT_SECONDS + 120
    },
    visibility_timeout=settings.BATCH_QUESTION_TIMEOUT_SECONDS + 120,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.BATCH_WORKER_CONCURRENCY,
    task_routes={
        "backend.tasks.batch_tasks.process_batch_job": {"queue": "batches"},
        "backend.tasks.batch_tasks.dispatch_batch_jobs": {"queue": "celery"},
    },
    beat_schedule={
        "batch-outbox": {
            "task": "backend.tasks.batch_tasks.dispatch_batch_jobs",
            "schedule": 5.0,
        }
    },
)
