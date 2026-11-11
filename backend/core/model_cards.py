"""Deterministic Google-style cards; generation never calls models or reads files."""

import hashlib
import io
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.core.execution import resolved_api_url
from backend.core.llm.openai_llm import OpenAILLM
from backend.core.question_sets import check_no_credentials, project_or_404
from backend.core.response_trace import redact
from backend.core.versioning import (
    document_manifest,
    fingerprint,
    indexing_settings,
    lock_project,
)
from backend.db.models import (
    DataSource,
    DocumentVersion,
    IndexVersion,
    ModelCardVersion,
)

TEMPLATE_VERSION = 1
GOOGLE_REFERENCE = "https://arxiv.org/abs/1810.03993"
CONTEXT_LABELS = {
    "intended_use": "Назначение",
    "users": "Пользователи",
    "domain": "Предметная область",
    "owner": "Ответственный",
    "model_developer": "Разработчик базовой модели",
    "model_revision": "Ревизия базовой модели",
    "license": "Условия использования",
    "training_data": "Данные обучения",
    "evaluation_data": "Данные оценки",
    "factors": "Дополнительные факторы",
    "ethical_considerations": "Дополнительные риски",
    "recommendations": "Рекомендации владельца проекта",
}


def _provider(url):
    """No userinfo, provider paths, query parameters or fragments in public cards."""
    try:
        parsed = urlsplit(resolved_api_url(url))
        return parsed.hostname or "Не указан"
    except ValueError:
        return "Не указан"


def _clean(value, project):
    return redact(
        value,
        (
            project.llm_api_key,
            project.embedding_api_key,
            settings.OPENAI_API_KEY,
            settings.QDRANT_API_KEY,
        ),
    )


def source_snapshot(db, project):
    configuration = {
        name: getattr(project, name)
        for name in (
            "llm_model",
            "embedding_model",
            "embedding_dimension",
            "chunk_size",
            "chunk_overlap",
            "chunking_strategy",
            "split_by",
            "extract_tables",
            "top_k",
            "max_context_chars",
        )
    }
    configuration.update(
        llm_provider=_provider(project.llm_api_url),
        embedding_provider=_provider(project.embedding_api_url),
        # Detect endpoint changes without exporting a potentially private URL path.
        llm_endpoint_sha256=fingerprint(resolved_api_url(project.llm_api_url)),
        embedding_endpoint_sha256=fingerprint(
            resolved_api_url(project.embedding_api_url)
        ),
        system_prompt_sha256=hashlib.sha256(
            (project.system_prompt or OpenAILLM.DEFAULT_SYSTEM_PROMPT).encode()
        ).hexdigest(),
        score_threshold=None,
        max_attempts=1,
        generation_parameters="model, messages, stream=false; остальные значения задаёт провайдер",
    )
    sources = (
        db.query(DataSource).filter_by(project_id=project.id, is_active=True).all()
    )
    documents = []
    for source in sources:
        version = (
            db.get(DocumentVersion, source.document_version_id)
            if source.document_version_id
            else None
        )
        valid = version is not None and version.project_id == project.id
        documents.append(
            {
                "source_id": str(source.id),
                "document_version_id": str(version.id) if valid else None,
                "sha256": version.sha256 if valid else None,
                "size_bytes": version.size_bytes if valid else None,
            }
        )
    documents.sort(key=lambda item: item["source_id"])
    # Index status is informational; a completed build does not invalidate a card.
    index = None
    if all(item["document_version_id"] for item in documents):
        # Existing index fingerprints include names, but names never enter the card.
        # Use the application's canonical document manifest and settings.
        manifest = document_manifest(db, project.id)
        digest = fingerprint(
            {"settings": indexing_settings(project), "documents": manifest}
        )
        index = (
            db.query(IndexVersion)
            .filter_by(project_id=project.id, fingerprint=digest)
            .first()
        )
    source = {
        "schema_version": 1,
        "template_version": TEMPLATE_VERSION,
        "project_id": str(project.id),
        "project_name": _clean(project.name, project),
        "configuration": _clean(configuration, project),
        "documents": documents,
        "documents_sha256": fingerprint(documents),
    }
    digest = fingerprint(source)
    source["index"] = (
        {
            "id": str(index.id),
            "status": index.status,
            "chunks_count": index.chunks_count,
        }
        if index
        else None
    )
    return source, digest


def _text(context, field):
    return (
        f"{CONTEXT_LABELS[field]}: {context[field] or 'Не указано владельцем проекта.'}"
    )


def sections_for(source, context, measurements):
    c = source["configuration"]
    docs = source["documents"]
    missing = sum(item["document_version_id"] is None for item in docs)
    index = source["index"]
    index_text = (
        f"Индекс {index['id']}; состояние в БД: {index['status']}; фрагментов: {index['chunks_count']}. "
        "Доступность Qdrant при составлении карточки не проверялась."
        if index
        else "Индекс для текущего состава и настроек в БД не найден. Генерация карточки не строит индекс."
    )
    metrics = [
        "Автоматические численные метрики качества не подключены. transport_stub проверяет формат передачи данных, а не качество ответа. Оценка близости Qdrant не является вероятностью правильного ответа.",
        "Рекомендуемые показатели: корректность ответов по эталону, подтверждённость фактическим контекстом, полнота поиска и корректный отказ при отсутствии сведений. Технические ошибки следует учитывать отдельно; для времени ответа указывать медиану и p95.",
    ]
    quantitative = [
        "Результаты автоматической оценки качества отсутствуют. Карточка не выполняет эксперименты и не выводит качество из настроек."
    ]
    if measurements:
        metrics.append(
            "Ниже приведены результаты, введённые человеком. Система не проверяет методику и достоверность этих измерений; они относятся только к зафиксированной конфигурации карточки."
        )
        for m in measurements:
            quantitative.append(
                f"Ручная оценка — {m['name']}: {m['value']:g} {m['unit']}. "
                f"Объём выборки: {m['sample_size']}; набор: {m['dataset']}. "
                f"Методика: {m['method']}. Дата: {m['measured_at']}; проверяющий: {m['reviewer']}."
            )
    else:
        quantitative.append(
            "Корректность, подтверждённость, полнота поиска, время ответа и стоимость: не оценивались в рамках этой карточки. Отсутствие измерения не означает нулевой результат."
        )
    values = [
        (
            "details",
            "Сведения о модели",
            "Model Details",
            [
                f"Проект: {source['project_name']}. Тип: генеративная языковая модель в составе RAG-ассистента. LLM: {c['llm_model']}; эмбеддер: {c['embedding_model']}; размерность: {c['embedding_dimension']}.",
                f"Провайдер LLM: {c['llm_provider']}; эмбеддингов: {c['embedding_provider']}. Совместимость API не устанавливает разработчика модели.",
                _text(context, "model_developer"),
                _text(context, "model_revision"),
                _text(context, "license"),
                _text(context, "owner"),
                f"Чанкинг: {c['chunking_strategy']}, {c['chunk_size']} символов, перекрытие {c['chunk_overlap']}. top_k: {c['top_k']}; лимит найденного контекста: {c['max_context_chars']} символов. Порог поиска по умолчанию не задан; одна техническая попытка. Переопределения отдельных запросов не входят в эту конфигурацию.",
                f"Отпечаток системного промпта: {c['system_prompt_sha256']}. Сам текст промпта не публикуется. Temperature, seed и лимит ответа приложение явно не задаёт. Точная повторяемость генерации не гарантирована.",
                index_text,
            ],
        ),
        (
            "use",
            "Предполагаемое использование",
            "Intended Use",
            [
                _text(context, "intended_use"),
                _text(context, "users"),
                _text(context, "domain"),
                "Вход: вопрос и найденные фрагменты документов; в обычном чате также история. Выход: текст ответа и список источников. Существенные выводы следует сверять с первоисточником. Автономное принятие решений с серьёзными последствиями не проверено.",
            ],
        ),
        (
            "factors",
            "Факторы влияющие на качество",
            "Factors",
            [
                "Качество зависит от полноты и актуальности документов, языка и формулировки вопроса, эмбеддера, разбиения текста, top_k и ограничения контекста. Обрезка контекста может удалить нужный факт. История диалога способна менять ответ.",
                "Следует отдельно оценивать прямые вопросы, вопросы по нескольким фрагментам и вопросы без ответа. Различия по языкам, демографическим группам и пересечениям условий не оценены автоматически.",
                _text(context, "factors"),
            ],
        ),
        ("metrics", "Метрики", "Metrics", metrics),
        (
            "evaluation",
            "Данные для оценки",
            "Evaluation Data",
            [
                _text(context, "evaluation_data"),
                "Проект поддерживает версии проверочных вопросов, эталоны и подтверждения, проверку человеком и разделение tuning/test. Наличие этих возможностей не доказывает наличие репрезентативной выборки. Итоговые вопросы нельзя использовать для подбора настроек; эталоны не должны попадать в промпт.",
            ],
        ),
        (
            "training",
            "Данные обучения",
            "Training Data",
            [
                _text(context, "training_data"),
                "В проекте не выполняется обучение весов базовой модели. Пользовательские документы служат базой поиска; построение эмбеддингов и передача контекста не являются дообучением LLM. Сведения о предварительном обучении необходимо подтверждать документацией разработчика выбранной модели.",
                f"В текущем составе: {len(docs)} источников. Без версии документа: {missing}. Отпечаток состава: {source['documents_sha256']}. Имена, пути и содержимое файлов в карточку не включены.",
            ],
        ),
        (
            "quantitative",
            "Количественный анализ",
            "Quantitative Analyses",
            quantitative,
        ),
        (
            "ethics",
            "Этические аспекты",
            "Ethical Considerations",
            [
                "Возможны выдуманные факты, предвзятость и инструкции злоумышленника внутри документов. Список источников сам по себе не подтверждает все утверждения ответа. Специальная устойчивость к prompt injection не установлена.",
                "При внешних API текст передаётся провайдеру эмбеддингов, а найденный контекст и сообщения — провайдеру LLM. Необходимо определить права на документы, доступ, сроки хранения и удаления. Карточка не подтверждает безопасность развёртывания.",
                _text(context, "ethical_considerations"),
            ],
        ),
        (
            "caveats",
            "Ограничения и рекомендации",
            "Caveats and Recommendations",
            [
                "Поддерживаются TXT, DOCX и PDF с извлекаемым текстом; ZIP используется для загрузки файлов. OCR, локальный HuggingFace-эмбеддер и потоковые ответы не реализованы. Сложная вёрстка и таблицы требуют проверки извлечения.",
                "При ошибке поиска RAG возвращает ошибку без вызова LLM. При успешном поиске без фрагментов LLM вызывается с пустым контекстом (no_context); отказ от ответа не гарантирован.",
                "После смены модели, промпта, эмбеддера, поиска или документов необходимо обновить карточку и повторить оценку. Сохраняйте версии данных, настройки и условия эксперимента. Статусы индекса берутся из БД и не являются проверкой инфраструктуры.",
                _text(context, "recommendations"),
            ],
        ),
    ]
    return [
        {"key": key, "title": title, "english_title": english, "paragraphs": paragraphs}
        for key, title, english, paragraphs in values
    ]


def preview(db, project_id, payload):
    project = project_or_404(db, project_id)
    authored = payload.model_dump(mode="json", include={"context", "measurements"})
    check_no_credentials(authored, project)

    # Reject XML-invalid controls before exporting to Word, with no input echo.
    def invalid_text(value):
        if isinstance(value, dict):
            return any(invalid_text(item) for item in value.values())
        if isinstance(value, list):
            return any(invalid_text(item) for item in value)
        return isinstance(value, str) and bool(
            re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", value)
        )

    if invalid_text(authored):
        raise HTTPException(422, "Текст содержит недопустимые управляющие символы.")
    source, digest = source_snapshot(db, project)
    sections = sections_for(source, authored["context"], authored["measurements"])
    sections = _clean(sections, project)
    return {
        "id": None,
        "number": None,
        "created_at": datetime.now(UTC).isoformat(),
        "source_sha256": digest,
        "content_sha256": fingerprint(
            {"source": source, **authored, "sections": sections}
        ),
        "source_snapshot": source,
        **authored,
        "sections": sections,
        "is_stale": False,
        "changed_fields": [],
    }


def latest(db, project_id):
    return (
        db.query(ModelCardVersion)
        .filter_by(project_id=project_id)
        .order_by(ModelCardVersion.number.desc())
        .first()
    )


def create(db, project_id, payload):
    project_or_404(db, project_id)
    project = lock_project(db, project_id)
    db.refresh(project)
    current = latest(db, project_id)
    if (current.id if current else None) != payload.base_version_id:
        raise HTTPException(
            409, "Уже сохранена новая версия. Обновите список и повторите сохранение."
        )
    draft = preview(db, project_id, payload)
    if draft["source_sha256"] != payload.source_sha256:
        raise HTTPException(
            409,
            "Настройки или документы изменились. Обновите предпросмотр; результаты оценки нужно проверить заново.",
        )
    record = ModelCardVersion(
        project_id=project_id,
        number=current.number + 1 if current else 1,
        **{
            key: draft[key]
            for key in (
                "source_sha256",
                "content_sha256",
                "source_snapshot",
                "context",
                "measurements",
                "sections",
            )
        },
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Версия уже сохранена другим запросом. Обновите список."
        ) from exc
    db.refresh(record)
    return record


def scoped_card(db, project_id, card_id):
    project_or_404(db, project_id)
    card = (
        db.query(ModelCardVersion).filter_by(project_id=project_id, id=card_id).first()
    )
    if card is None:
        raise HTTPException(404, "Версия Model Card не найдена.")
    return card


def _created_at(card):
    value = card.created_at
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).isoformat()


def card_response(card, current_source, current_digest):
    previous = card.source_snapshot
    changes = []
    if previous["project_name"] != current_source["project_name"]:
        changes.append("project_name")
    for key, value in current_source["configuration"].items():
        if previous["configuration"].get(key) != value:
            changes.append(key)
    if previous["documents_sha256"] != current_source["documents_sha256"]:
        changes.append("documents")
    if previous.get("template_version") != TEMPLATE_VERSION:
        changes.append("template_version")
    return {
        "id": str(card.id),
        "number": card.number,
        "created_at": _created_at(card),
        "source_sha256": card.source_sha256,
        "content_sha256": card.content_sha256,
        "source_snapshot": card.source_snapshot,
        "context": card.context,
        "measurements": card.measurements,
        "sections": card.sections,
        "is_stale": card.source_sha256 != current_digest,
        "changed_fields": changes,
    }


def _plain(value):
    # Strip XML-invalid controls from automatic fields too (e.g. imported names).
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", "", str(value))


def markdown(card):
    # Escape authored text so Markdown/HTML cannot become active content in viewers.
    def escape(value):
        return re.sub(r"([\\`*_{}\[\]()#+.!|<>-])", r"\\\1", _plain(value))

    lines = [
        f"# Model Card — {escape(card.source_snapshot['project_name'])}",
        "",
        f"Версия {card.number} · {_created_at(card)}",
        "",
        f"Отпечаток конфигурации и данных: {card.source_sha256}",
        "",
        "Снимок на дату сохранения. Перед применением сверяйте его с текущим проектом.",
        "",
    ]
    for i, section in enumerate(card.sections, 1):
        lines += [f"## {i} {section['title']}", "", section["english_title"], ""]
        for paragraph in section["paragraphs"]:
            lines += [escape(paragraph), ""]
    lines += [
        "## Источники",
        "",
        f"Google Model Cards: {GOOGLE_REFERENCE}",
        "",
        f"Версия шаблона: {card.source_snapshot['template_version']}",
        f"Идентификатор проекта: {card.project_id}",
        f"Идентификатор карточки: {card.id}",
        f"Отпечаток содержимого: {card.content_sha256}",
        "",
    ]
    return "\n".join(lines)


def word_document(card):
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2)
    section.footer_distance = Cm(0.7)
    for name in ("Normal", "Title", "Heading 1"):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.color.rgb = RGBColor(0, 0, 0)
        if style.element.pPr is not None:
            for border in list(style.element.pPr.findall(qn("w:pBdr"))):
                style.element.pPr.remove(border)
    normal = doc.styles["Normal"]
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.widow_control = True
    doc.styles["Title"].font.size = Pt(22)
    doc.styles["Heading 1"].font.size = Pt(14)
    doc.add_paragraph(
        _plain(f"Model Card {card.source_snapshot['project_name']}"), "Title"
    )
    doc.add_paragraph(f"Версия {card.number} · {_created_at(card)}")
    doc.add_paragraph(f"Отпечаток конфигурации и данных: {card.source_sha256}")
    doc.add_paragraph(
        "Снимок на дату сохранения. Перед применением сверяйте его с текущим проектом."
    )
    for i, item in enumerate(card.sections, 1):
        doc.add_paragraph(f"{i} {item['title']}", "Heading 1")
        p = doc.add_paragraph(item["english_title"])
        p.paragraph_format.keep_with_next = True
        for run in p.runs:
            run.italic = True
        for paragraph in item["paragraphs"]:
            doc.add_paragraph(_plain(paragraph))
    doc.add_paragraph("Источники", "Heading 1")
    doc.add_paragraph(f"Google Model Cards: {GOOGLE_REFERENCE}")
    doc.add_paragraph(f"Версия шаблона: {card.source_snapshot['template_version']}")
    doc.add_paragraph(f"Проект: {card.project_id}; карточка: {card.id}")
    doc.add_paragraph(f"Отпечаток содержимого: {card.content_sha256}")
    footer = section.footer.paragraphs[0]
    footer.alignment = 2
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    doc.core_properties.title = "Model Card"
    doc.core_properties.author = ""
    result = io.BytesIO()
    doc.save(result)
    return result.getvalue()
