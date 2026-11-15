import logging

from .eval_io import digest, read_rows, timestamp

log = logging.getLogger(__name__)


def split_event(event_id):
    return "holdout" if int(digest(event_id)[:8], 16) % 4 == 0 else "tuning"


def validate_case(case):
    for key in ("id", "snapshot", "dataset_version", "event_id", "question", "query_language",
                "question_type", "expected", "split", "review_status"):
        if not isinstance(case[key], str) or not case[key].strip():
            raise ValueError(f"Invalid case field: {key}")
    if case["split"] not in ("tuning", "holdout") or case["review_status"] not in ("draft", "reviewed"):
        raise ValueError("Invalid split or review status")
    if not isinstance(case.get("filters", {}), dict):
        raise ValueError("filters must be an object")
    if "question" in case.get("filters", {}):
        raise ValueError("filters cannot include question")
    if case["expected"] == "answer":
        if not case["facts"] or not all(isinstance(f, str) and f.strip() for f in case["facts"]):
            raise ValueError("Answerable case requires facts")
        if not case["evidence"]:
            raise ValueError("Answerable case requires evidence")
        for source in case["evidence"]:
            for key in ("source_id", "quote", "language"):
                if not isinstance(source[key], str) or not source[key].strip():
                    raise ValueError(f"Invalid evidence field: {key}")
    elif case["expected"] != "abstain" or not case.get("coverage_gap"):
        raise ValueError("Abstention cases need a documented coverage gap")
    cutoff = case.get("cutoff")
    if cutoff:
        field = case["cutoff_field"]
        if field not in ("published_at", "captured_at"):
            raise ValueError("Invalid cutoff field")
        limit = timestamp(cutoff)
        for source in case.get("evidence", []):
            if not source.get(field) or timestamp(source[field]) > limit:
                raise ValueError("Evidence is undated or after cutoff")


def index_rows(rows):
    indexed = {}
    for row in rows:
        if row["id"] in indexed:
            raise ValueError(f"Duplicate ID: {row['id']}")
        indexed[row["id"]] = row
    return indexed


def checked_dataset(path):
    cases = list(index_rows(read_rows(path)).values())
    if not cases:
        raise ValueError("Dataset is empty")
    splits = {}
    for case in cases:
        validate_case(case)
        event = case["event_id"]
        if splits.setdefault(event, case["split"]) != case["split"]:
            raise ValueError(f"Event crosses splits: {event}")
    for field in ("snapshot", "dataset_version"):
        if len({case[field] for case in cases}) != 1:
            raise ValueError(f"Dataset must have one {field}")
    reviewed = sum(case["review_status"] == "reviewed" for case in cases)
    log.info("Validated %d cases (%d reviewed) across %d events from %s",
             len(cases), reviewed, len(splits), path)
    return cases
