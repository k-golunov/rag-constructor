"""Preview, immutable history and exports scoped to a RAG project."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.core import model_cards as cards
from backend.core.question_sets import project_or_404
from backend.db.database import get_db
from backend.db.model_card_schemas import CardCreate, CardDraft
from backend.db.models import ModelCardVersion

router = APIRouter(
    prefix="/api/v1/public/provider/projects/{project_id}/model-cards",
    tags=["model-cards"],
)


@router.post("/preview")
def preview(project_id: UUID, payload: CardDraft, db: Session = Depends(get_db)):
    return cards.preview(db, project_id, payload)


@router.post("", status_code=201)
def create(project_id: UUID, payload: CardCreate, db: Session = Depends(get_db)):
    card = cards.create(db, project_id, payload)
    source, digest = cards.source_snapshot(db, project_or_404(db, project_id))
    return cards.card_response(card, source, digest)


@router.get("")
def listing(
    project_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    source, digest = cards.source_snapshot(db, project_or_404(db, project_id))
    query = db.query(ModelCardVersion).filter_by(project_id=project_id)
    items = (
        query.order_by(ModelCardVersion.number.desc()).offset(offset).limit(limit).all()
    )
    return {
        "total": query.count(),
        "items": [
            {
                key: value
                for key, value in cards.card_response(card, source, digest).items()
                if key
                in {
                    "id",
                    "number",
                    "created_at",
                    "source_sha256",
                    "is_stale",
                    "changed_fields",
                }
            }
            for card in items
        ],
    }


@router.get("/{card_id}")
def detail(project_id: UUID, card_id: UUID, db: Session = Depends(get_db)):
    card = cards.scoped_card(db, project_id, card_id)
    source, digest = cards.source_snapshot(db, project_or_404(db, project_id))
    return cards.card_response(card, source, digest)


@router.get("/{card_id}/export")
def export(
    project_id: UUID,
    card_id: UUID,
    format: Literal["md", "docx"] = "md",
    db: Session = Depends(get_db),
):
    card = cards.scoped_card(db, project_id, card_id)
    content = (
        cards.markdown(card).encode("utf-8")
        if format == "md"
        else cards.word_document(card)
    )
    media = (
        "text/markdown; charset=utf-8"
        if format == "md"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="model-card-{project_id}-v{card.number}.{format}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
