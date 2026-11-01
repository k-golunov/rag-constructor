"""Handoff contracts and test checks, separate from collecting RAG answers."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.evaluation_checks import create_check, check_result
from backend.core.evaluators import REGISTRY, checksum
from backend.core.metric_inputs import scoped_result, build_metric_input
from backend.db.database import get_db
from backend.db.metric_schemas import (
    EvaluationCheckRequest,
    MetricInput,
    MetricResult,
    MetricInputEnvelope,
    EvaluationCheckResponse,
    EvaluationCheckList,
    EvaluatorCatalog,
)
from backend.db.models import EvaluationCheck, Project

router = APIRouter(
    prefix="/api/v1/public/provider/projects/{project_id}",
    tags=["evaluation-contracts"],
)


def project_exists(db, project_id):
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Проект не найден.")


@router.get("/metric-evaluators", response_model=EvaluatorCatalog)
def descriptors(project_id: UUID, db: Session = Depends(get_db)):
    project_exists(db, project_id)
    return {
        "metrics_status": "not_implemented",
        "evaluators": [
            value.descriptor.model_dump(mode="json") for value in REGISTRY.values()
        ],
    }


@router.get("/metric-contracts")
def contracts(project_id: UUID, db: Session = Depends(get_db)):
    project_exists(db, project_id)
    return {
        "input": MetricInput.model_json_schema(),
        "result": MetricResult.model_json_schema(),
        "schema_version": 1,
    }


@router.get(
    "/batches/{batch_id}/results/{result_id}/metric-input",
    response_model=MetricInputEnvelope,
)
def metric_input(
    project_id: UUID,
    batch_id: UUID,
    result_id: UUID,
    execution_run_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    result = scoped_result(db, project_id, batch_id, result_id)
    sample = build_metric_input(db, result, execution_run_id)
    return {
        "input_sha256": checksum(sample.model_dump(mode="json")),
        "input": sample.model_dump(mode="json"),
    }


@router.post(
    "/batches/{batch_id}/results/{result_id}/evaluation-checks",
    status_code=201,
    response_model=EvaluationCheckResponse,
)
async def evaluate(
    project_id: UUID,
    batch_id: UUID,
    result_id: UUID,
    payload: EvaluationCheckRequest,
    db: Session = Depends(get_db),
):
    result = scoped_result(db, project_id, batch_id, result_id)
    return await create_check(db, result, payload)


@router.get(
    "/batches/{batch_id}/results/{result_id}/evaluation-checks",
    response_model=EvaluationCheckList,
)
def checks(
    project_id: UUID,
    batch_id: UUID,
    result_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    result = scoped_result(db, project_id, batch_id, result_id)
    query = db.query(EvaluationCheck).filter_by(result_id=result.id)
    return {
        "total": query.count(),
        "items": [
            check_result(record)
            for record in query.order_by(
                EvaluationCheck.created_at.desc(), EvaluationCheck.id
            )
            .offset(offset)
            .limit(limit)
        ],
    }
