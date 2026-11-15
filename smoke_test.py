"""Run generate, run, and score with fictional SQL rows and simulated HTTP."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from xml.etree import ElementTree
from tempfile import TemporaryDirectory
from unittest.mock import patch

from news_eval import http_client
import rag_eval


HERE = Path(__file__).resolve().parent
EXAMPLES = {
    "en": ("When does the first Monday ferry from Alder Bay leave?", "It leaves at 08:00."),
    "ar": ("متى تفتح مكتبة السرو أيام السبت؟", "تفتح الساعة العاشرة صباحًا."),
    "fr": ("À quelle heure ouvre le musée de Clairval le dimanche ?", "Il ouvre à neuf heures."),
}


def sample_dummy():
    # SQLite tests the join, grouping, and bounds. Adapt PostgreSQL's parameter
    # syntax and ANY(array) for the in-memory database.
    sql = (HERE / "sample.sql").read_text(encoding="utf-8")
    sql = sql.replace("= ANY(%(languages)s)", "IN ('ar', 'en', 'fr')")
    for key in ("min_page_id", "max_page_id", "seed", "limit_per_language"):
        sql = sql.replace(f"%({key})s", f":{key}")
    connection = sqlite3.connect(":memory:")
    try:
        connection.row_factory = sqlite3.Row
        connection.create_function("md5", 1, lambda value: hashlib.md5(value.encode()).hexdigest())
        connection.executescript((HERE / "tests" / "fixtures" / "smoke-seed.sql").read_text(encoding="utf-8"))
        params = {"min_page_id": 10, "max_page_id": 12, "limit_per_language": 1, "seed": "smoke"}
        rows = [dict(row) for row in connection.execute(sql, params)]
        assert {row["language"] for row in rows} == set(EXAMPLES)
        bounded = list(connection.execute(sql, params | {"max_page_id": 11}))
        assert {row["page_id"] for row in bounded} == {10, 11}
        return rows
    finally:
        connection.close()


class Response(io.BytesIO):
    status = 200

    def __init__(self, body):
        super().__init__(json.dumps(body).encode())


def smoke(output):
    rows = sample_dummy()
    by_question = {EXAMPLES[row["language"]][0]: row for row in rows}
    articles = output / "articles.jsonl"
    drafts = output / "drafts.jsonl"
    reviewed = output / "reviewed.jsonl"
    runs = output / "runs.jsonl"
    report = output / "scorecard.json"
    config = output / "config.json"
    config.write_text((HERE / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    rag_eval.write_rows(articles, rows)

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/chat/completions"):
            payload = json.loads(request.data)
            data = json.loads(payload["messages"][-1]["content"])
            if payload["model"] == "mock-drafter":
                article, = data["articles"]
                question, fact = EXAMPLES[article["language"]]
                result = {"cases": [{"question": question, "query_language": article["language"],
                                     "question_type": "same_language", "facts": [fact],
                                     "evidence": [{"source_id": article["source_id"],
                                                   "quote": article["text"]}]}]}
            else:
                bad = data["answer"] == "Il ouvre à sept heures."
                result = {"criteria": dict.fromkeys(rag_eval.CRITERIA, True) |
                                      {"facts": not bad, "grounded": not bad},
                          "reason": "Opening time differs from the passage." if bad else "Fixture matches."}
            return Response({"choices": [{"message": {"content": json.dumps(result)}}]})
        if request.full_url.endswith("/ask"):
            question = json.loads(request.data)["question"]
            row = by_question[question]
            language = row["language"]
            answer = "Il ouvre à sept heures." if language == "fr" else EXAMPLES[language][1]
            return Response({"answer": answer, "request_id": str(row["page_id"]),
                             "citations": [f"p{row['page_id']}:c0"]})
        request_id = request.full_url.rsplit("/", 1)[-1]
        row = next(row for row in rows if str(row["page_id"]) == request_id)
        return Response({"request_id": request_id, "passages": [
            {"source_id": f"p{request_id}:c0", "text": row["text"]}]})

    def cli(*args):
        status = rag_eval.main(["--config", str(config), "--log-file", str(output / "pipeline.log"),
                                *map(str, args)])
        assert status == 0, (args, status)

    with patch.object(http_client, "urlopen", side_effect=fake_urlopen):
        cli("generate", "--articles", articles, "--snapshot", "archive-2026-11",
            "--version", "synthetic", "--model", "mock-drafter", "--output", drafts)
        candidates = rag_eval.read_rows(drafts)
        assert len(candidates) == 3
        assert all(case["evidence"][0]["source_unit"] == "article" for case in candidates)
        assert all(case["evidence"][0]["captured_at"].endswith("+00:00") for case in candidates)
        # Exercise development scoring with every fictional case.
        rag_eval.write_rows(reviewed, (case | {"split": "tuning"} for case in candidates))
        cli("run", "--dataset", reviewed, "--output", runs)
        cli("score", "--dataset", reviewed, "--runs", runs, "--model", "mock-judge",
            "--mode", "development", "--output", report)
    stats = json.loads(report.read_text(encoding="utf-8"))["overall"]
    assert (stats["passed"], stats["failed"], stats["unscored"]) == (2, 1, 0)
    assert stats["retrieval_count"] == 0 and stats["recall_at_k"] is None
    chart = ElementTree.parse(report.with_suffix(".svg"))
    chart_text = " ".join(chart.getroot().itertext())
    assert "66.7%" in chart_text and "Unavailable" in chart_text
    logs = (output / "pipeline.log").read_text(encoding="utf-8")
    for stage in ("archive_sampling", "question_drafting", "dataset_review", "staging_runner",
                  "response_scoring", "scorecard"):
        assert f"[news_eval.{stage}]" in logs, stage
    print("Synthetic smoke test passed: 2 expected passes, 1 deliberate failure, 0 unscored.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Save test artifacts in a new directory")
    args = parser.parse_args()
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        smoke(args.output_dir)
    else:
        with TemporaryDirectory() as directory:
            smoke(Path(directory))
