import logging
from time import perf_counter
from urllib.error import HTTPError
from urllib.parse import quote

from .eval_io import digest
from .http_client import HTTP_FAILURES, request_json

log = logging.getLogger(__name__)


def get_field(data, path):
    for key in path.split("."):
        data = data[key]
    return data


def run_case(case, staging):
    fields = staging.get("response_fields", {})
    trace_fields = staging.get("trace_fields", {})
    timeout = staging.get("timeout", 30)
    token_env = staging.get("token_env")
    row = {"id": case["id"], "case_digest": digest(case), "build": staging["build"], "status": None}
    log.info("Sending case %s to staging", case["id"])
    start = perf_counter()
    try:
        payload = {staging.get("question_field", "question"): case["question"]}
        if case.get("filters"):
            payload[staging.get("filters_field", "filters")] = case["filters"]
        row["status"], response = request_json(staging["url"], token_env, timeout, payload)
    except HTTP_FAILURES as error:
        if isinstance(error, HTTPError):
            row["status"] = error.code
        row.update(outcome="endpoint_error", error=type(error).__name__)
    row["latency_ms"] = round((perf_counter() - start) * 1000, 2)
    if "outcome" in row:
        log.warning("Case %s endpoint failed (%s, HTTP %s)", case["id"], row["error"], row["status"])
        return row
    try:
        row["answer"] = get_field(response, fields.get("answer", "answer"))
        row["citations"] = get_field(response, fields.get("citations", "citations"))
        if not isinstance(row["answer"], str) or not isinstance(row["citations"], list):
            raise ValueError("Invalid staging response")
    except HTTP_FAILURES:
        row.update(outcome="endpoint_error", error="invalid_response")
        log.warning("Case %s returned an invalid staging response", case["id"])
        return row
    try:
        request_id = get_field(response, fields.get("request_id", "request_id"))
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Invalid request ID")
        row["request_id"] = request_id
        url = staging["trace_url"].format(request_id=quote(request_id, safe=""))
        _, trace = request_json(url, staging.get("trace_token_env", token_env), timeout)
        if get_field(trace, trace_fields.get("request_id", "request_id")) != request_id:
            raise ValueError("Mismatched trace")
        passages = get_field(trace, trace_fields.get("passages", "passages"))
        if not isinstance(passages, list) or any(
            not isinstance(passage["source_id"], str) or not isinstance(passage["text"], str)
            for passage in passages
        ):
            raise ValueError("Invalid passages")
        row.update(outcome="ok", passages=passages)
        log.info("Case %s returned %d passages in %.1f ms (answer request)",
                 case["id"], len(passages), row["latency_ms"])
    except HTTP_FAILURES:
        row.update(outcome="missing_trace")
        log.warning("Case %s has no usable evidence trace", case["id"])
    return row
