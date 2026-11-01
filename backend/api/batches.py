"""Batch execution API. Creation pins inputs; execution can be resumed explicitly."""

from uuid import UUID
from typing import Literal
from fastapi.responses import StreamingResponse
from backend.core import metrics_analysis as analysis

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core import batch as service
from backend.core.batch_queue import enqueue, publish_job, cancel
from backend.db.batch_schemas import BatchCreate
from backend.db.database import get_db
from backend.db.models import BatchRun, BatchQuestionResult, BatchVariant

router = APIRouter(
    prefix="/api/v1/public/provider/projects/{project_id}/batches", tags=["batches"]
)


@router.post("", status_code=201)
def create(project_id: UUID, payload: BatchCreate, db: Session = Depends(get_db)):
    try:
        batch = service.create_batch(db, project_id, payload)
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(
            422,
            exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc
    return service.batch_detail(db, batch)


@router.get("")
def listing(
    project_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    service.project_or_404(db, project_id)
    query = db.query(BatchRun).filter_by(project_id=project_id)
    return {
        "total": query.count(),
        "items": [
            service.batch_summary(db, item)
            for item in query.order_by(BatchRun.created_at.desc(), BatchRun.id)
            .offset(offset)
            .limit(limit)
            .all()
        ],
    }


@router.post("/preview")
def preview(project_id: UUID, payload: BatchCreate, db: Session = Depends(get_db)):
    try:
        return analysis.preview(db, project_id, payload)
    except ValidationError as exc:
        raise HTTPException(
            422,
            exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc


@router.get("/configurations")
def configurations(
    project_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return analysis.saved_configurations(db, project_id, offset, limit)


@router.get("/{batch_id}")
def detail(project_id: UUID, batch_id: UUID, db: Session = Depends(get_db)):
    return service.batch_detail(db, service.batch_or_404(db, project_id, batch_id))


@router.get("/{batch_id}/results")
def results(
    project_id: UUID,
    batch_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    variant_id: UUID | None = None,
    status: Literal["pending", "running", "success", "error"] | None = None,
    db: Session = Depends(get_db),
):
    batch = service.batch_or_404(db, project_id, batch_id)
    examples = analysis.example_map(db, batch)
    query = (
        db.query(BatchQuestionResult)
        .join(BatchVariant)
        .filter(BatchVariant.batch_run_id == batch_id)
    )
    if variant_id is not None:
        query = query.filter(BatchQuestionResult.variant_id == variant_id)
    if status is not None:
        query = query.filter(BatchQuestionResult.status == status)
    return {
        "total": query.count(),
        "items": [
            service.result_detail(db, result, examples)
            for result in query.order_by(
                BatchVariant.position,
                BatchQuestionResult.repeat_number,
                BatchQuestionResult.question_position,
            )
            .offset(offset)
            .limit(limit)
            .all()
        ],
    }


@router.post("/{batch_id}/execute", status_code=202)
def execute(project_id: UUID, batch_id: UUID, db: Session = Depends(get_db)):
    batch = service.batch_or_404(db, project_id, batch_id)
    job = enqueue(db, batch)
    if job is not None:
        publish_job(db, job)
    db.refresh(batch)
    return service.batch_detail(db, batch)


@router.post("/{batch_id}/results/{result_id}/retry", status_code=202)
def retry(
    project_id: UUID, batch_id: UUID, result_id: UUID, db: Session = Depends(get_db)
):
    batch = service.batch_or_404(db, project_id, batch_id)
    job = enqueue(db, batch, retry_result_id=result_id)
    if job is not None:
        publish_job(db, job)
    db.refresh(batch)
    return service.batch_detail(db, batch)


@router.post("/{batch_id}/cancel", status_code=202)
def cancel_batch(project_id: UUID, batch_id: UUID, db: Session = Depends(get_db)):
    batch = service.batch_or_404(db, project_id, batch_id)
    return service.batch_detail(db, cancel(db, batch))


@router.post("/{batch_id}/rerun", status_code=201)
def rerun(project_id: UUID, batch_id: UUID, db: Session = Depends(get_db)):
    source = service.batch_or_404(db, project_id, batch_id)
    return service.batch_detail(db, service.rerun_batch(db, source))


@router.get("/{batch_id}/export")
def export(
    project_id: UUID,
    batch_id: UUID,
    format: Literal["json", "csv"] = "json",
    db: Session = Depends(get_db),
):
    service.batch_or_404(db, project_id, batch_id)
    return StreamingResponse(
        analysis.export_data(db.get_bind(), project_id, batch_id, format),
        media_type=(
            "application/json" if format == "json" else "text/csv; charset=utf-8"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="rag-analysis-{batch_id}.{format}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
