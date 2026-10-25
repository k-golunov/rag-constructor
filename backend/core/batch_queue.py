"""Database outbox, serial per-batch execution and recoverable Celery delivery."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_

from backend.config import settings
from backend.core import batch as execution
from backend.db.models import (
    BatchJob,
    BatchRun,
    BatchQuestionResult,
    BatchVariant,
    BatchResultAttempt,
    ExecutionRun,
)

ACTIVE = {"queued", "published", "running"}


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def policy():
    timeout = settings.BATCH_QUESTION_TIMEOUT_SECONDS
    return {
        "question_timeout_seconds": timeout,
        "max_attempts": settings.BATCH_JOB_MAX_ATTEMPTS,
        "retry_delay_seconds": settings.BATCH_RETRY_DELAY_SECONDS,
        "soft_time_limit": timeout + 30,
        "time_limit": timeout + 60,
    }


def job_info(job):
    if job is None:
        return None
    return {
        "id": job.id,
        "result_id": job.result_id,
        "status": job.status,
        "execution_attempts": job.execution_attempts,
        "policy": job.policy,
        "available_at": job.available_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_code": job.error_code,
    }


def next_pending(db, batch):
    return (
        db.query(BatchQuestionResult)
        .join(BatchVariant)
        .filter(
            BatchVariant.batch_run_id == batch.id,
            BatchQuestionResult.status == "pending",
        )
        .order_by(
            BatchVariant.position,
            BatchQuestionResult.repeat_number,
            BatchQuestionResult.question_position,
        )
        .first()
    )


def add_job(db, batch, result):
    job = BatchJob(
        batch_run_id=batch.id, result_id=result.id, policy=policy(), available_at=now()
    )
    db.add(job)
    db.flush()
    batch.active_job_id = job.id
    batch.status, batch.finished_at = "queued", None
    return job


def enqueue(db, batch, retry_result_id=None):
    # The row lock covers API requests and the worker's continuation scheduling.
    batch = (
        db.query(BatchRun)
        .filter_by(id=batch.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    active = db.get(BatchJob, batch.active_job_id) if batch.active_job_id else None
    if active is not None and active.status in ACTIVE:
        if batch.cancel_requested:
            raise HTTPException(409, "Дождитесь завершения отмены перед продолжением.")
        if retry_result_id is not None and active.result_id != retry_result_id:
            raise HTTPException(409, "Пакет уже выполняется.")
        db.commit()
        return active
    if retry_result_id is not None:
        result = (
            db.query(BatchQuestionResult)
            .join(BatchVariant)
            .filter(
                BatchVariant.batch_run_id == batch.id,
                BatchQuestionResult.id == retry_result_id,
            )
            .first()
        )
        if result is None:
            raise HTTPException(404, "Результат не найден.")
        if result.status != "error":
            raise HTTPException(409, "Повтор доступен только после ошибки.")
    else:
        result = next_pending(db, batch)
        if batch.cancel_requested and active is not None:
            cancelled_result = db.get(BatchQuestionResult, active.result_id)
            if cancelled_result.status in {"running", "pending"} or (
                cancelled_result.status == "error"
                and retryable_result(db, cancelled_result)
            ):
                result = cancelled_result
        if result is None:
            # A pre-queue interrupted batch may still contain a running result.
            result = (
                db.query(BatchQuestionResult)
                .join(BatchVariant)
                .filter(
                    BatchVariant.batch_run_id == batch.id,
                    BatchQuestionResult.status == "running",
                )
                .first()
            )
    batch.cancel_requested = False
    if result is None:
        update_final_status(db, batch)
        db.commit()
        return None
    job = add_job(db, batch, result)
    db.commit()  # Durable before attempting any broker I/O.
    return job


def cancel(db, batch):
    batch = (
        db.query(BatchRun)
        .filter_by(id=batch.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    job = db.get(BatchJob, batch.active_job_id) if batch.active_job_id else None
    if batch.status in {"completed", "completed_with_errors"} and (
        job is None or job.status not in ACTIVE
    ):
        db.commit()
        return batch
    batch.cancel_requested = True
    if job is not None and job.status == "running":
        batch.status = "cancelling"
    else:
        if job is not None and job.status in ACTIVE:
            job.status, job.finished_at = "cancelled", now()
        batch.status, batch.finished_at = "cancelled", now()
    db.commit()
    return batch


def update_final_status(db, batch):
    statuses = execution.status_counts(db, batch)
    batch.status = (
        "cancelled"
        if batch.cancel_requested
        else (
            "pending"
            if statuses["pending"] > 0
            else "completed_with_errors" if statuses["error"] > 0 else "completed"
        )
    )
    batch.finished_at = None if batch.status == "pending" else now()


def publish_job(db, job, sender=None):
    """Commit a delivery lease before send. Beat repairs the commit/send crash window."""
    job = (
        db.query(BatchJob)
        .filter_by(id=job.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    timestamp = now()
    if (
        job.status not in {"queued", "published", "running"}
        or aware(job.available_at) > timestamp
    ):
        db.commit()
        return False
    if job.status in {"published", "running"}:
        if job.lease_expires_at and aware(job.lease_expires_at) > timestamp:
            db.commit()
            return False
    job.status, job.dispatched_at = "published", timestamp
    job.lease_expires_at = timestamp + timedelta(seconds=job.policy["time_limit"] + 30)
    db.commit()
    if sender is None:
        from backend.tasks.batch_tasks import process_batch_job

        sender = process_batch_job.apply_async
    try:
        sender(
            args=[str(job.id)],
            task_id=str(job.id),
            queue="batches",
            retry=False,
            soft_time_limit=job.policy["soft_time_limit"],
            time_limit=job.policy["time_limit"],
        )
        return True
    except Exception:
        db.rollback()
        job = (
            db.query(BatchJob)
            .filter_by(id=job.id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        # A delivery may have reached Redis despite a lost publish acknowledgement.
        if job.status == "published":
            job.status, job.error_code = "queued", "broker_unavailable"
            job.available_at = now() + timedelta(
                seconds=job.policy["retry_delay_seconds"]
            )
            db.commit()
        return False


def dispatch_due(db, sender=None, limit=100):
    # IDs only: never hold locks for multiple jobs while publishing another.
    candidates = (
        db.query(BatchJob.id)
        .filter(
            or_(
                and_(BatchJob.status == "queued", BatchJob.available_at <= now()),
                and_(
                    BatchJob.status.in_({"published", "running"}),
                    BatchJob.lease_expires_at <= now(),
                ),
            )
        )
        .order_by(BatchJob.available_at)
        .limit(limit)
        .all()
    )
    return sum(
        publish_job(db, db.get(BatchJob, identifier), sender)
        for (identifier,) in candidates
    )


def retryable_result(db, result):
    attempt = (
        db.query(BatchResultAttempt)
        .filter_by(result_id=result.id)
        .order_by(BatchResultAttempt.number.desc())
        .first()
    )
    if attempt is None:
        return True
    run = db.get(ExecutionRun, attempt.execution_run_id)
    if run.error_code in {"execution_timeout", "execution_interrupted"}:
        return True
    errors = (run.response_trace or {}).get("errors", [])
    return bool(
        errors
        and (
            errors[-1].get("retryable")
            or errors[-1].get("type") in {"SoftTimeLimitExceeded", "TimeLimitExceeded"}
        )
    )


async def perform_job(db, job_id, *, embedder_factory, store_factory, generate):
    job = db.get(BatchJob, UUID(str(job_id)))
    if job is None or job.status not in ACTIVE:
        return "ignored"
    batch = db.get(BatchRun, job.batch_run_id)
    with execution.execution_lock(db, batch):
        execution.recover_interrupted(db, batch)
        batch = (
            db.query(BatchRun)
            .filter_by(id=batch.id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        job = (
            db.query(BatchJob)
            .filter_by(id=job.id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        if job.status not in ACTIVE or batch.active_job_id != job.id:
            return "ignored"
        if aware(job.available_at) > now():
            return "waiting"
        result = db.get(BatchQuestionResult, job.result_id)
        if batch.cancel_requested:
            job.status, job.finished_at = "cancelled", now()
            update_final_status(db, batch)
            db.commit()
            return "cancelled"
        if (
            result.status != "success"
            and job.execution_attempts < job.policy["max_attempts"]
        ):
            job.execution_attempts += 1
            job.status, job.started_at, job.error_code = "running", now(), None
            job.lease_expires_at = now() + timedelta(
                seconds=job.policy["time_limit"] + 30
            )
            batch.status = "running"
            db.commit()
            try:
                await execution.execute_batch(
                    db,
                    batch,
                    embedder_factory=embedder_factory,
                    store_factory=store_factory,
                    generate=generate,
                    target_result_id=result.id,
                    retry_result_id=result.id if result.status == "error" else None,
                    lock_held=True,
                    question_timeout_seconds=job.policy["question_timeout_seconds"],
                    execution_metadata={
                        "id": str(job.id),
                        "policy": job.policy,
                        "attempt": job.execution_attempts,
                    },
                )
            except BaseException:
                db.rollback()
                # Worker loss is recovered by redelivery/outbox after the execution lock releases.
                raise
        db.refresh(batch)
        db.refresh(result)
        if result.status == "success":
            job.status, job.finished_at = "success", now()
        elif (
            not batch.cancel_requested
            and job.execution_attempts < job.policy["max_attempts"]
            and retryable_result(db, result)
        ):
            job.status, job.started_at, job.error_code = (
                "queued",
                None,
                "question_retry",
            )
            job.available_at = now() + timedelta(
                seconds=job.policy["retry_delay_seconds"]
                * 2 ** (job.execution_attempts - 1)
            )
            batch.status = "queued"
            db.commit()
            return "retry"
        else:
            result.status = "error"
            job.status, job.finished_at, job.error_code = (
                "error",
                now(),
                "question_failed",
            )
        # Serialize completion and the next outbox row with API cancel/resume.
        db.flush()
        batch = (
            db.query(BatchRun)
            .filter_by(id=batch.id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        pending = next_pending(db, batch)
        if pending is not None and not batch.cancel_requested:
            add_job(db, batch, pending)
        else:
            update_final_status(db, batch)
        db.commit()
        return job.status
