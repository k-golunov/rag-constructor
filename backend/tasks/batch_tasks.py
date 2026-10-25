"""Celery deliveries contain only an outbox UUID; PostgreSQL owns progress."""

import asyncio

from fastapi import HTTPException

from backend.core.batch_queue import perform_job, dispatch_due
from backend.core.factories import build_embedder, build_vector_store
from backend.core.rag import _generate_traced
from backend.db.database import SessionLocal, engine
from backend.tasks.worker import celery_app
from celery.signals import worker_process_init


@worker_process_init.connect
def reset_worker_connections(**kwargs):
    engine.dispose(close=False)


@celery_app.task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
    max_retries=3,
    soft_time_limit=210,
    time_limit=240,
)
def process_batch_job(self, job_id):
    with SessionLocal() as db:
        try:
            asyncio.run(
                perform_job(
                    db,
                    job_id,
                    embedder_factory=build_embedder,
                    store_factory=build_vector_store,
                    generate=_generate_traced,
                )
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # Another delivery owns the advisory lock; it remains authoritative.
        except Exception:
            # The durable outbox re-delivers expired leases, including after hard kills.
            # Do not put provider exceptions or credentials into Redis task results.
            raise self.retry(
                countdown=10, exc=RuntimeError("batch_worker_error")
            ) from None
        dispatch_due(db)


@celery_app.task(ignore_result=True)
def dispatch_batch_jobs():
    with SessionLocal() as db:
        return dispatch_due(db)
