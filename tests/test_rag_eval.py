import argparse
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import rag_eval as ev
from news_eval import question_drafting
from news_eval import response_scoring
from news_eval import staging_runner


def fixture(kind="cross_language", expected="answer"):
    evidence = [{"source_id": "s1", "language": "en", "quote": "The port reopened Monday.",
                 "published_at": "2025-01-06"}] if expected == "answer" else []
    return {"id": kind, "event_id": "event", "snapshot": "snapshot", "dataset_version": "v1",
            "question": "متى فتح الميناء؟", "query_language": "ar", "answer_language": "ar",
            "question_type": kind, "expected": expected, "coverage_gap": "Verified archive range",
            "facts": ["The port reopened Monday."] if evidence else [], "evidence": evidence,
            "filters": {"edition": "Alexandria"}, "split": "holdout", "review_status": "reviewed"}


def grade(**overrides):
    return {"criteria": dict.fromkeys(ev.CRITERIA, True) | overrides, "reason": "Fixture judgment"}


class PipelineTests(unittest.TestCase):
    staging = {"url": "https://staging.example/ask", "build": "test",
               "trace_url": "https://staging.example/traces/{request_id}"}

    def run_with_passages(self, case, date="2025-01-06"):
        response = {"answer": "أعيد فتح الميناء يوم الاثنين.", "citations": ["s1"],
                    "request_id": "req/1"}
        trace = {"request_id": "req/1", "passages": [{"source_id": "s1", "language": "en",
                 "text": "The port reopened Monday.", "published_at": date}]}
        with patch.object(staging_runner, "request_json", side_effect=[(200, response), (200, trace)]) as request:
            row = ev.run_case(case, self.staging)
        self.assertEqual(request.call_args_list[0].args[3],
                         {"question": case["question"], "filters": case["filters"]})
        self.assertTrue(request.call_args_list[1].args[0].endswith("/req%2F1"))
        return row

    def score(self, case, row, model_grade, mode="development", humans=None):
        if mode == "development":
            case = case | {"split": "tuning"}
            row = row | {"case_digest": ev.digest(case)}
        with patch.object(response_scoring, "ask_model", return_value=model_grade) as judge:
            results, _ = ev.score_dataset([case], {case["id"]: row}, humans or {}, {}, "mock", mode)
        return ev.scorecard(results, 5)["overall"], judge

    def test_cross_language_and_invalid_citation(self):
        case = fixture()
        row = self.run_with_passages(case)
        report, _ = self.score(case, row, grade())
        self.assertEqual((report["pass_rate"], report["recall_at_k"]), (1, 1))
        report, _ = self.score(case, row, grade(citations=False))
        self.assertEqual(report["failed"], 1)

    def test_late_passage_is_ineligible(self):
        case = fixture("as_of") | {"cutoff": "2025-01-06", "cutoff_field": "published_at"}
        row = self.run_with_passages(case, "2025-01-07")
        report, judge = self.score(case, row, grade())
        self.assertEqual(judge.call_args.args[-1]["passages"], [])
        self.assertEqual(report["failed"], 1)

    def test_abstention_and_incorrect_refusal(self):
        case = fixture("missing_coverage", "abstain")
        row = self.run_with_passages(case) | {"answer": "الأرشيف لا يحتوي على أدلة كافية.", "citations": []}
        report, _ = self.score(case, row, grade())
        self.assertEqual(report["passed"], 1)
        case = fixture()
        row = self.run_with_passages(case) | {"answer": "الأرشيف لا يحتوي على أدلة كافية.", "citations": []}
        report, _ = self.score(case, row, grade(facts=False, abstention=False))
        self.assertEqual(report["incorrect_refusals"], 1)

    def test_timeout_fails_and_missing_trace_is_unscored(self):
        case = fixture()
        with patch.object(staging_runner, "request_json", side_effect=TimeoutError):
            row = ev.run_case(case, self.staging)
        report, judge = self.score(case, row, grade())
        self.assertEqual((report["endpoint_errors"], report["pass_rate"]), (1, 0))
        judge.assert_not_called()
        response = {"answer": "answer", "citations": [], "request_id": "req"}
        error = HTTPError("https://staging.example", 404, "Not found", {}, None)
        with patch.object(staging_runner, "request_json", side_effect=[(200, response), error]):
            row = ev.run_case(case, self.staging)
        report, _ = self.score(case, row, grade())
        self.assertEqual((row["outcome"], report["unscored"], report["pass_rate"]),
                         ("missing_trace", 1, None))

    def test_unverified_judge_requires_human_for_release(self):
        case = fixture()
        row = self.run_with_passages(case)
        report, _ = self.score(case, row, grade(), "release")
        self.assertEqual(report["unscored"], 1)
        human = grade() | {key: row[key] for key in ("id", "request_id", "case_digest", "build")}
        report, _ = self.score(case, row, grade(citations=False), "release", {case["id"]: human})
        self.assertEqual(report["pass_rate"], 1)

    def test_draft_validates_quotes_and_remains_unreviewed(self):
        source = {"source_id": "s1", "event_id": "event", "language": "en", "text": "Port open."}
        candidate = {"question": "هل الميناء مفتوح؟", "query_language": "ar",
                     "question_type": "cross_language", "facts": ["The port is open."],
                     "evidence": [{"source_id": "s1", "quote": "Port open."}]}
        bad = candidate | {"evidence": [{"source_id": "s1", "quote": "Port closed."}]}
        args = argparse.Namespace(version="v1", snapshot="snapshot", max_chars=200,
                                  model="mock", languages=["ar", "en"])
        with patch.object(question_drafting, "ask_model", return_value={"cases": [candidate, bad]}):
            drafts, errors = ev.draft_cases(args, {}, [source])
        self.assertEqual(len(drafts), 1)
        self.assertEqual(next(iter(drafts))["review_status"], "draft")
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
