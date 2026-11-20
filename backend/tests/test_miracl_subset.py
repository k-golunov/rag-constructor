import gzip
import json
from uuid import uuid5

import pytest

from backend.core.ingestion.parser import TextParser
from backend.datasets import miracl
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag

TOPICS = {
    "q1": "Когда основан Казанский университет?",
    "q2": "Какая река протекает через Самару?",
    "q3": "Вопрос без релевантных фрагментов",
    "q4": "  когда основан  казанский университет? ",
    "q5": "Вопрос с потерянным фрагментом",
}
QRELS = [
    ("q1", "d1#0", 1),
    ("q1", "d2#0", 0),
    ("q1", "d3#0", 0),
    ("q2", "d4#0", 1),
    ("q2", "d1#0", 0),
    ("q3", "d2#0", 0),
    ("q4", "d1#0", 1),
    ("q4", "d2#0", 0),
    ("q4", "d3#0", 0),
    ("q5", "lost#0", 1),
]
PASSAGES = [
    {
        "docid": "d1#0",
        "title": "Казанский университет",
        "text": "Университет основан в 1804 году.\r\nЭто один из старейших вузов.",
    },
    {"docid": "d2#0", "title": "Казань", "text": "Казань стоит на Волге."},
    {"docid": "d3#0", "title": "", "text": "Университетов в Казани много."},
    {"docid": "d4#0", "title": "Самара", "text": "Самара стоит на Волге и Самарке."},
]


def lines(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


@pytest.fixture
def source(tmp_path):
    topics = tmp_path / "topics.tsv"
    topics.write_text(
        "".join(f"{key}\t{value}\n" for key, value in TOPICS.items()), encoding="utf-8"
    )
    qrels = tmp_path / "qrels.tsv"
    qrels.write_text(
        "".join(f"{q}\tQ0\t{d}\t{r}\n" for q, d, r in QRELS), encoding="utf-8"
    )
    first = tmp_path / "docs-0.jsonl.gz"
    with gzip.open(first, "wt", encoding="utf-8") as stream:
        stream.write(lines(PASSAGES[:2]))
    second = tmp_path / "docs-1.jsonl"
    second.write_text("\n" + lines(PASSAGES[2:]), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(
        lines({"query_id": key, "answer": "В 1804 году."} for key in ("q1", "q4")),
        encoding="utf-8",
    )
    return {
        "topics_path": topics,
        "qrels_path": qrels,
        "corpus_paths": [first, second],
        "answers_path": answers,
        "out_dir": tmp_path / "subset",
    }


def normalized(text):
    return " ".join(text.casefold().split())


def test_subset_files_and_labels(source):
    summary = miracl.build_subset(**source, negatives=2)
    out = source["out_dir"]
    assert summary["label"] == miracl.LABEL
    assert json.loads((out / "subset.json").read_text(encoding="utf-8")) == summary
    assert summary["queries"] == 2
    assert summary["reference_answers"] == 1
    assert summary["skipped_queries"] == [
        {"query_id": "q5", "missing_passages": ["lost#0"]}
    ]
    assert {item["file"] for item in summary["sources"]} == {
        "topics.tsv",
        "qrels.tsv",
        "docs-0.jsonl.gz",
        "docs-1.jsonl",
        "answers.jsonl",
    }
    examples = json.loads((out / "questions.json").read_text(encoding="utf-8"))[
        "examples"
    ]
    assert len({normalized(e["question"]) for e in examples}) == 2
    files = sorted(path.name for path in (out / "corpus").iterdir())
    assert files == ["d1_0.txt", "d2_0.txt", "d3_0.txt", "d4_0.txt"]
    for example in examples:
        assert example["category"] == miracl.CATEGORY
        assert example["split"] in {"tuning", "test"}
        assert "MIRACL-ru" in example["explanation"]
        for proof in example["evidence"]:
            text = TextParser().parse(str(out / "corpus" / proof["file_name"]))
            start = proof["start_char"]
            assert text[start : start + len(proof["quote"])] == proof["quote"]
            assert "\r" not in text
    by_question = {normalized(e["question"]): e for e in examples}
    university = by_question["когда основан казанский университет?"]
    assert university["reference_answer"] == "В 1804 году."
    assert "подготовлен отдельно" in university["explanation"]
    assert "адаптированная подвыборка" in university["category"]
    river = by_question["какая река протекает через самару?"]
    assert river["reference_answer"] is None
    assert river["id"] == str(uuid5(miracl.QUESTION_NAMESPACE, "q2"))


def test_selection_is_deterministic_and_respects_limit(source, tmp_path):
    first = miracl.build_subset(**{**source, "out_dir": tmp_path / "a"}, limit=1)
    second = miracl.build_subset(**{**source, "out_dir": tmp_path / "b"}, limit=1)
    assert first == second
    assert first["queries"] == 1
    assert (tmp_path / "a" / "questions.json").read_text(encoding="utf-8") == (
        tmp_path / "b" / "questions.json"
    ).read_text(encoding="utf-8")


def test_refuses_to_mix_with_a_previous_subset(source):
    miracl.build_subset(**source)
    with pytest.raises(ValueError, match="не пуст"):
        miracl.build_subset(**source, limit=1)


@pytest.mark.parametrize(
    ("target", "content", "message"),
    [
        ("topics_path", "q1 без табуляции\n", "табуляция"),
        ("topics_path", "q1\tраз\nq1\tдва\n", "повторяется"),
        ("qrels_path", "q1 Q0 d1#0\n", "relevance"),
        ("qrels_path", "q1 Q0 d1#0 1\nq1 Q0 d1#0 0\n", "дважды"),
        ("answers_path", '{"query_id": "q1"}\n', "answer"),
    ],
)
def test_malformed_inputs_fail_with_location(source, target, content, message):
    source[target].write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message) as error:
        miracl.build_subset(**source)
    assert source[target].name in str(error.value)
    assert not source["out_dir"].exists()


def test_corpus_rows_need_docid_and_text(source):
    source["corpus_paths"][1].write_text('{"docid": "d3#0"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="docid, text"):
        miracl.build_subset(**source)


def test_empty_selection_is_an_error(source):
    source["qrels_path"].write_text("q1 Q0 lost#0 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ни одного запроса"):
        miracl.build_subset(**source)


@pytest.mark.parametrize(("limit", "negatives"), [(0, 3), (10001, 3), (5, -1)])
def test_invalid_limit_or_negatives(source, limit, negatives):
    with pytest.raises(ValueError, match="limit"):
        miracl.build_subset(**source, limit=limit, negatives=negatives)


def test_cli(source, capsys):
    miracl.main(
        [
            *("--topics", str(source["topics_path"])),
            *("--qrels", str(source["qrels_path"])),
            *("--corpus", *map(str, source["corpus_paths"])),
            *("--out", str(source["out_dir"])),
        ]
    )
    assert miracl.LABEL in capsys.readouterr().out
    with pytest.raises(SystemExit):
        miracl.main(
            [
                *("--topics", "t", "--qrels", "q", "--corpus", "c"),
                *("--out", "o", "--limit", "0"),
            ]
        )


def test_subset_imports_into_a_question_set(source, rag):
    rag.embedder.embed.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    miracl.build_subset(**source)
    out = source["out_dir"]
    for path in sorted((out / "corpus").iterdir()):
        uploaded = pipeline.upload(rag, path.read_bytes(), path.name)
        assert uploaded.json()["status"] == "completed", uploaded.text
    sets_url = f"{pipeline.BASE}/projects/{rag.project_id}/question-sets"
    created = rag.client.post(sets_url, json={"name": miracl.LABEL}).json()
    response = rag.client.post(
        f"{sets_url}/{created['id']}/import",
        params={"base_version_id": created["version"]["id"]},
        files={"file": ("questions.json", (out / "questions.json").read_bytes())},
    )
    assert response.status_code == 201, response.text
    examples = response.json()["examples"]
    assert len(examples) == 2
    assert all(
        proof["document_version_id"] for e in examples for proof in e["evidence"]
    )
