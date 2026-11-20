import argparse
import gzip
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

LABEL = "Адаптированная подвыборка MIRACL-ru"
CATEGORY = "MIRACL-ru (адаптированная подвыборка)"
QUESTION_NAMESPACE = uuid5(NAMESPACE_URL, "rag-constructor/miracl-ru")


def _open(path):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def _digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _order(query_id):
    return hashlib.sha256(query_id.encode("utf-8")).hexdigest()


def _lines(path):
    with _open(path) as stream:
        for number, line in enumerate(stream, start=1):
            if line.strip():
                yield f"{Path(path).name}:{number}", line.rstrip("\n")


def _json_rows(path, fields):
    for where, line in _lines(path):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{where}: строка не является JSON.") from exc
        if not isinstance(row, dict) or any(field not in row for field in fields):
            raise ValueError(f"{where}: нужны поля {', '.join(fields)}.")
        yield where, row


def read_topics(path):
    topics = {}
    for where, line in _lines(path):
        query_id, _, query = (part.strip() for part in line.partition("\t"))
        if not query_id or not query:
            raise ValueError(f"{where}: ожидается идентификатор, табуляция и запрос.")
        if query_id in topics:
            raise ValueError(f"{where}: запрос {query_id} повторяется.")
        topics[query_id] = query
    return topics


def read_qrels(path):
    judged = defaultdict(dict)
    for where, line in _lines(path):
        parts = line.split()
        if len(parts) != 4 or not parts[3].lstrip("-").isdigit():
            raise ValueError(f"{where}: ожидается «query_id Q0 docid relevance».")
        query_id, _, doc_id, relevance = parts
        if doc_id in judged[query_id]:
            raise ValueError(f"{where}: пара {query_id} / {doc_id} размечена дважды.")
        judged[query_id][doc_id] = int(relevance)
    return judged


def read_answers(path):
    answers = {}
    for where, row in _json_rows(path, ("query_id", "answer")):
        query_id = str(row["query_id"])
        if query_id in answers:
            raise ValueError(f"{where}: ответ на запрос {query_id} повторяется.")
        if answer := str(row["answer"]).strip():
            answers[query_id] = answer
    return answers


def read_passages(paths, wanted):
    found = {}
    for path in paths:
        for _, row in _json_rows(path, ("docid", "text")):
            if row["docid"] in wanted:
                found.setdefault(row["docid"], row)
                if len(found) == len(wanted):
                    return found
    return found


def file_name_for(doc_id):
    return re.sub(r"[^0-9A-Za-z_-]", "_", doc_id) + ".txt"


def _clean(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def build_subset(
    topics_path,
    qrels_path,
    corpus_paths,
    out_dir,
    limit=50,
    negatives=3,
    answers_path=None,
):
    if not 1 <= limit <= 10000 or negatives < 0:
        raise ValueError("limit должен быть от 1 до 10000, negatives не меньше 0.")
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"Каталог {out} не пуст.")
    topics = read_topics(topics_path)
    judged = read_qrels(qrels_path)
    answers = read_answers(answers_path) if answers_path else {}

    candidates = []
    for query_id in sorted(topics, key=_order):
        labels = judged.get(query_id, {})
        positives = sorted(doc for doc, rel in labels.items() if rel > 0)
        if positives:
            hard = sorted((doc for doc, rel in labels.items() if rel <= 0), key=_order)
            candidates.append((query_id, positives, hard))

    wanted = {doc for _, pos, neg in candidates for doc in (*pos, *neg)}
    passages = read_passages(corpus_paths, wanted)

    selected, skipped, seen = [], [], set()
    for query_id, positives, hard in candidates:
        if len(selected) == limit:
            break
        missing = [doc for doc in positives if doc not in passages]
        normalized = " ".join(topics[query_id].casefold().split())
        if missing:
            skipped.append({"query_id": query_id, "missing_passages": missing})
        elif normalized not in seen:
            seen.add(normalized)
            available = [doc for doc in hard if doc in passages]
            selected.append((query_id, positives, available[:negatives]))
    if not selected:
        raise ValueError("Не отобрано ни одного запроса.")

    names = {}
    for _, positives, hard in selected:
        for doc_id in (*positives, *hard):
            name = file_name_for(doc_id)
            if names.setdefault(name, doc_id) != doc_id:
                raise ValueError(
                    f"Фрагменты {doc_id} и {names[name]} дают одно имя файла."
                )

    corpus_dir = out / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    offsets = {}
    for name, doc_id in sorted(names.items()):
        row = passages[doc_id]
        title, text = _clean(row.get("title") or ""), _clean(row["text"])
        if not text:
            raise ValueError(f"Фрагмент {doc_id} пуст.")
        header = f"{title}\n\n" if title else ""
        (corpus_dir / name).write_text(
            header + text + "\n", encoding="utf-8", newline="\n"
        )
        offsets[doc_id] = (len(header), text)

    examples = []
    for query_id, positives, _ in selected:
        reference = answers.get(query_id)
        explanation = f"MIRACL-ru, запрос {query_id}."
        if reference:
            explanation += " Эталон подготовлен отдельно, в MIRACL его нет."
        examples.append(
            {
                "id": str(uuid5(QUESTION_NAMESPACE, query_id)),
                "question": topics[query_id],
                "reference_answer": reference,
                "evidence": [
                    {
                        "file_name": file_name_for(doc_id),
                        "quote": offsets[doc_id][1],
                        "start_char": offsets[doc_id][0],
                    }
                    for doc_id in positives
                ],
                "category": CATEGORY,
                "unanswerable": False,
                "split": "test" if int(_order(query_id), 16) % 10 >= 4 else "tuning",
                "explanation": explanation,
            }
        )

    (out / "questions.json").write_text(
        json.dumps(
            {"schema_version": 1, "examples": examples}, ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    summary = {
        "label": LABEL,
        "queries": len(examples),
        "passages": len(names),
        "limit": limit,
        "negatives_per_query": negatives,
        "reference_answers": sum(1 for item in examples if item["reference_answer"]),
        "skipped_queries": skipped,
        "sources": [
            {"file": Path(path).name, "sha256": _digest(path)}
            for path in (
                topics_path,
                qrels_path,
                *corpus_paths,
                *([answers_path] if answers_path else []),
            )
        ],
    }
    (out / "subset.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Собирает адаптированную подвыборку MIRACL-ru для импорта в набор вопросов."
    )
    parser.add_argument("--topics", required=True, help="TSV с запросами: id и текст")
    parser.add_argument("--qrels", required=True, help="Разметка в формате TREC")
    parser.add_argument(
        "--corpus", required=True, nargs="+", help="JSONL или JSONL.GZ корпуса"
    )
    parser.add_argument("--out", required=True, help="Каталог результата")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--negatives", type=int, default=3)
    parser.add_argument(
        "--answers", help="JSONL с полями query_id и answer для эталонных ответов"
    )
    args = parser.parse_args(argv)
    try:
        summary = build_subset(
            args.topics,
            args.qrels,
            args.corpus,
            args.out,
            limit=args.limit,
            negatives=args.negatives,
            answers_path=args.answers,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"{LABEL}: {summary['queries']} запросов, {summary['passages']} фрагментов, "
        f"эталонов {summary['reference_answers']}, пропущено {len(summary['skipped_queries'])}"
    )


if __name__ == "__main__":
    main()
