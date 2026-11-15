from collections import defaultdict
import logging
import math

log = logging.getLogger(__name__)


def summarize(items, k):
    passed = sum(grade["pass"] is True for _, _, grade in items)
    unscored = sum(grade["pass"] is None for _, _, grade in items)
    latencies = sorted(row["latency_ms"] for _, row, _ in items if "latency_ms" in row)
    recalls = []
    for case, row, _ in items:
        evidence = case.get("evidence", [])
        expected = {source["source_id"] for source in evidence}
        # Article-level seed IDs and production chunk IDs are different units.
        if (expected and all(source.get("source_unit") in (None, "chunk") for source in evidence)
                and row.get("outcome") == "ok"):
            found = {passage["source_id"] for passage in row["passages"][:k]}
            recalls.append(len(expected & found) / len(expected))
    return {"total": len(items), "passed": passed, "failed": len(items) - passed - unscored,
            "unscored": unscored, "complete": bool(items) and not unscored,
            "pass_rate": passed / len(items) if items and not unscored else None,
            "endpoint_errors": sum(g["outcome"] == "endpoint_error" for _, _, g in items),
            "judge_errors": sum(bool(g.get("judge_error")) for _, _, g in items),
            "unsupported_answers": sum(g.get("criteria", {}).get("grounded") is False for _, _, g in items),
            "incorrect_refusals": sum(c["expected"] == "answer" and
                                      g.get("criteria", {}).get("abstention") is False for c, _, g in items),
            "p95_latency_ms": latencies[math.ceil(len(latencies) * .95) - 1] if latencies else None,
            "context_k": k, "retrieval_count": len(recalls),
            "hit_at_k": sum(r > 0 for r in recalls) / len(recalls) if recalls else None,
            "recall_at_k": sum(recalls) / len(recalls) if recalls else None}


def scorecard(results, k):
    groups = defaultdict(list)
    for item in results:
        case = item[0]
        source_languages = "+".join(sorted({e["language"] for e in case.get("evidence", [])})) or "none"
        groups[f"language:{case['query_language']}"].append(item)
        groups[f"language_pair:{case['query_language']}->{source_languages}"].append(item)
        groups[f"type:{case['question_type']}"].append(item)
    overall = summarize(results, k)
    log.info("Scorecard: %d passed, %d failed, %d unscored across %d cases",
             overall["passed"], overall["failed"], overall["unscored"], overall["total"])
    return {"overall": overall,
            "breakdown": {group: summarize(items, k) for group, items in sorted(groups.items())}}
