"""Collection planning and exports. Metric scores are deliberately absent."""

import csv
import io
import json
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.core import batch as service
from backend.core.question_sets import project_or_404
from backend.core.rag import _execution_secrets
from backend.core.response_trace import redact
from backend.core.versioning import fingerprint, indexing_settings
from backend.db.models import (
    BatchQuestionResult,
    BatchVariant,
    ConfigurationVersion,
    IndexVersion,
    QuestionSet,
    QuestionSetVersion,
)

PARAMETERS = [
    "top_k",
    "max_context_chars",
    "score_threshold",
    "chunk_size",
    "chunk_overlap",
    "embedding_model",
    "embedding_dimension",
    "embedding_api_url",
    "llm_model",
    "llm_api_url",
    "system_prompt",
    "max_attempts",
]


def differences(settings):
    return [
        {"parameter": key, "values": [value.get(key) for value in settings]}
        for key in PARAMETERS
        if len({fingerprint(value.get(key)) for value in settings}) > 1
    ]


def plan_checksum(version, examples, plans, repeats):
    return fingerprint(
        {
            "version_id": str(version.id),
            "version_sha256": version.sha256,
            "examples": [item["id"] for item in examples],
            "repeats": repeats,
            "variants": [
                {
                    "name": plan["name"],
                    "snapshot": plan["snapshot"],
                    "settings": plan["settings"],
                    "index_settings": indexing_settings(plan["project"]),
                }
                for plan in plans
            ],
        }
    )


def preview(db, project_id, payload):
    _, version, examples, plans = service.prepare_batch(db, project_id, payload)
    owners, output, needed = {}, [], set()
    for position, plan in enumerate(plans):
        spec = indexing_settings(plan["project"])
        digest = fingerprint({"settings": spec, "documents": version.documents})
        index = (
            db.query(IndexVersion)
            .filter_by(project_id=project_id, fingerprint=digest)
            .first()
        )
        build = index is None or index.status != "ready"
        if build:
            needed.add(digest)
        output.append(
            {
                "name": plan["name"],
                "applied_settings": plan["settings"],
                "source_configuration_version_id": (
                    str(payload.variants[position].configuration_version_id)
                    if payload.variants[position].configuration_version_id
                    else None
                ),
                "index": {
                    "fingerprint": digest,
                    "version_id": str(index.id) if index else None,
                    "status": index.status if index else "not_created",
                    "requires_build": build,
                    "shared_with": owners.get(digest),
                    "document_count": len(version.documents),
                },
            }
        )
        owners.setdefault(digest, plan["name"])
    return {
        "metrics_status": "not_implemented",
        "preview_sha256": plan_checksum(version, examples, plans, payload.repeats),
        "question_count": len(examples),
        "result_count": len(examples) * len(plans) * payload.repeats,
        "indexes_to_build": len(needed),
        "variants": output,
        "differences": differences([plan["settings"] for plan in plans]),
    }


def saved_configurations(db, project_id, offset, limit):
    project_or_404(db, project_id)
    query = db.query(ConfigurationVersion).filter(
        ConfigurationVersion.project_id == project_id,
        ConfigurationVersion.id.in_(db.query(BatchVariant.configuration_version_id)),
    )
    total = query.count()
    items = []
    for config in (
        query.order_by(ConfigurationVersion.created_at.desc(), ConfigurationVersion.id)
        .offset(offset)
        .limit(limit)
    ):
        source = (
            db.query(BatchVariant).filter_by(configuration_version_id=config.id).first()
        )
        items.append(
            {
                "id": str(config.id),
                "name": source.name,
                "sha256": config.sha256,
                "created_at": config.created_at,
                "applied_settings": config.settings,
            }
        )
    return {"total": total, "items": items}


def example_map(db, batch):
    version = db.get(QuestionSetVersion, batch.question_set_version_id)
    return {item["id"]: item for item in version.examples}


def metadata(db, batch):
    version = db.get(QuestionSetVersion, batch.question_set_version_id)
    selected = set(batch.selection["example_ids"])
    variants = service.variants(db, batch)
    return {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc),
        "metrics": {"status": "not_implemented", "scores": None},
        "evaluation_contract": {
            "input_schema_version": 1,
            "result_schema_version": 1,
            "mode": "transport_validation_only",
        },
        "batch": service.batch_detail(db, batch),
        "question_set": {
            "id": version.question_set_id,
            "version_id": version.id,
            "number": version.number,
            "sha256": version.sha256,
            "documents_sha256": version.documents_sha256,
            "documents": version.documents,
            "selected_examples": [e for e in version.examples if e["id"] in selected],
        },
        "configuration_versions": [
            {"id": config.id, "sha256": config.sha256, "settings": config.settings}
            for identifier in dict.fromkeys(
                v.configuration_version_id for v in variants
            )
            for config in [db.get(ConfigurationVersion, identifier)]
        ],
        "index_versions": [
            {
                "id": index.id,
                "fingerprint": index.fingerprint,
                "collection_name": index.collection_name,
                "status": index.status,
                "settings": index.settings,
                "documents": index.documents,
                "documents_sha256": index.documents_sha256,
            }
            for identifier in dict.fromkeys(v.index_version_id for v in variants)
            for index in [db.get(IndexVersion, identifier)]
        ],
    }


def csv_safe(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(jsonable_encoder(value), ensure_ascii=False)
    value = "" if value is None else str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + value
    return value


def export_data(bind, project_id, batch_id, format):
    """Open an independent snapshot so a slow download never outlives a request DB session."""
    with sessionmaker(bind=bind)() as db:
        if bind.dialect.name == "postgresql":
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            db.execute(text("SET TRANSACTION READ ONLY"))
        batch = service.batch_or_404(db, project_id, batch_id)
        secrets = _execution_secrets(project_or_404(db, project_id))

        def clean(value):
            return redact(jsonable_encoder(value), secrets)

        info = clean(metadata(db, batch))
        info["is_partial"] = bool(
            info["batch"]["counts"]["pending"]
            or info["batch"]["counts"]["running"]
            or batch.status not in {"completed", "completed_with_errors"}
        )
        examples = example_map(db, batch)
        query = (
            db.query(BatchQuestionResult)
            .join(BatchVariant)
            .filter(BatchVariant.batch_run_id == batch.id)
            .order_by(
                BatchVariant.position,
                BatchQuestionResult.repeat_number,
                BatchQuestionResult.question_position,
            )
        )
        if format == "json":
            header = json.dumps(info, ensure_ascii=False)
            yield header[:-1] + ', "results": ['
            first = True
            for result in query.yield_per(25):
                if not first:
                    yield ","
                first = False
                yield json.dumps(
                    clean(service.result_detail(db, result, examples)),
                    ensure_ascii=False,
                )
            yield "]}"
        else:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            columns = [
                "result_id",
                "variant",
                "repeat_number",
                "result_status",
                "attempt_number",
                "attempt_status",
                "question",
                "reference_answer",
                "unanswerable",
                "answer",
                "context",
                "retrieval_status",
                "error_code",
                "configuration_version_id",
                "index_version_id",
                "question_set_version_id",
                "retrieved_chunks",
                "evidence",
                "applied_settings",
                "response_trace",
                "jobs",
                "metric_input",
                "metric_input_sha256",
                "evaluation_checks",
                "export_metadata",
            ]
            yield "\ufeff"
            writer.writerow(columns)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            variants = {str(v["id"]): v for v in info["batch"]["variants"]}
            csv_info = {
                key: info[key]
                for key in (
                    "schema_version",
                    "exported_at",
                    "metrics",
                    "is_partial",
                    "evaluation_contract",
                )
            }
            csv_info.update(
                batch_id=info["batch"]["id"],
                batch_name=info["batch"]["name"],
                split=info["batch"]["selection"]["split"],
                question_set={
                    key: value
                    for key, value in info["question_set"].items()
                    if key not in {"selected_examples", "documents"}
                },
            )
            for result in query.yield_per(25):
                item = clean(service.result_detail(db, result, examples))
                variant = variants[item["variant_id"]]
                example = item["example"]
                for attempt in item["attempts"] or [{}]:
                    trace = attempt.get("response_trace") or {}
                    row = [
                        item["id"],
                        variant["name"],
                        item["repeat_number"],
                        item["status"],
                        attempt.get("number"),
                        attempt.get("status"),
                        example.get("question"),
                        example.get("reference_answer"),
                        example.get("unanswerable"),
                        trace.get("answer"),
                        trace.get("context"),
                        trace.get("retrieval_status"),
                        attempt.get("error_code"),
                        variant["configuration_version_id"],
                        variant["index_version_id"],
                        info["question_set"]["version_id"],
                        trace.get("retrieved_chunks"),
                        example.get("evidence"),
                        variant["applied_settings"],
                        trace,
                        item["jobs"],
                        item["metric_input"],
                        item["metric_input_sha256"],
                        item["evaluation_checks"],
                        csv_info,
                    ]
                    writer.writerow([csv_safe(cell) for cell in row])
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)
