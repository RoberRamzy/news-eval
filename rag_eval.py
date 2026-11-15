"""Command-line entry point for the news archive evaluation pipeline."""
import argparse
import json
import logging
from pathlib import Path
import sys

from news_eval.archive_sampling import load_articles
from news_eval.dataset_review import checked_dataset, index_rows
from news_eval.eval_io import digest, read_rows, write_rows
from news_eval.question_drafting import draft_cases
from news_eval.response_scoring import CRITERIA, RUBRIC_VERSION, score_dataset
from news_eval.score_chart import write_chart
from news_eval.scorecard import scorecard
from news_eval.staging_runner import run_case

log = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--log-file", help="Append progress logs to this file as well as stderr")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    source = generate.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql")
    source.add_argument("--articles", help="Use a saved snapshot sample in JSONL format")
    generate.add_argument("--snapshot", required=True)
    generate.add_argument("--version", required=True)
    generate.add_argument("--languages", nargs="+", default=["ar", "en", "fr"])
    generate.add_argument("--limit", type=int, default=30)
    generate.add_argument("--min-page-id", type=int, default=1)
    generate.add_argument("--max-page-id", type=int, default=10_000)
    generate.add_argument("--seed", default="news-v1")
    generate.add_argument("--max-chars", type=int, default=4000)
    generate.add_argument("--model", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--sample-output", help="Save source excerpts for reviewer audit")
    for command in ("run", "score"):
        sub = commands.add_parser(command)
        sub.add_argument("--dataset", required=True)
        sub.add_argument("--output", required=True)
        if command == "score":
            sub.add_argument("--runs", required=True)
            sub.add_argument("--humans")
            sub.add_argument("--baseline", help="Previous scorecard for the same dataset and rubric")
            sub.add_argument("--model", required=True)
            sub.add_argument("--mode", choices=("release", "development"), default="release")
            sub.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)
    handlers = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(level=args.log_level, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log.info("Starting %s", args.command)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.command == "generate":
        if min(args.limit, args.max_chars) < 1:
            parser.error("Sampling limits must be positive")
        if args.min_page_id < 1 or args.max_page_id < args.min_page_id:
            parser.error("Page ID range must be positive and ordered")
        articles = load_articles(args)
        if args.sample_output:
            write_rows(args.sample_output, ({**article, "text": article["text"][:args.max_chars]}
                                            for article in articles))
        cases, errors = draft_cases(args, config.get("generation", config.get("llm")), articles)
        write_rows(args.output, cases)
        write_rows(args.output + ".errors.jsonl", errors)
        log.info("Wrote %d drafts to %s and %d errors", len(cases), args.output, len(errors))
        return 0 if cases else 1

    cases = checked_dataset(args.dataset)
    if args.command == "run":
        if config["staging"]["snapshot"] != cases[0]["snapshot"]:
            raise ValueError("Staging snapshot differs from dataset snapshot")
        log.info("Running %d cases on staging build %s", len(cases), config["staging"]["build"])
        write_rows(args.output, (run_case(case, config["staging"]) for case in cases))
        log.info("Wrote staging results to %s", args.output)
        return 0

    if args.k < 1:
        parser.error("--k must be positive")
    runs = index_rows(read_rows(args.runs))
    humans = index_rows(read_rows(args.humans)) if args.humans else {}
    builds = {row["build"] for row in runs.values()}
    if len(builds) != 1:
        raise ValueError("Score one staging build at a time")
    results, calibration = score_dataset(cases, runs, humans, config.get("judge", config.get("llm")),
                                         args.model, args.mode)
    report = scorecard(results, args.k)
    report.update(mode=args.mode, dataset_version=cases[0]["dataset_version"],
                  dataset_digest=digest(cases), snapshot=cases[0]["snapshot"], build=next(iter(builds)),
                  judge_model=args.model, rubric_version=RUBRIC_VERSION, calibration=calibration)
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        for key in ("mode", "dataset_digest", "judge_model", "rubric_version"):
            if baseline[key] != report[key]:
                raise ValueError(f"Baseline differs in {key}")
        if baseline["overall"]["context_k"] != args.k:
            raise ValueError("Baseline uses a different k")
        before = baseline["overall"]["pass_rate"]
        after = report["overall"]["pass_rate"]
        report["comparison"] = {"baseline_build": baseline["build"],
                                "baseline_pass_rate": before,
                                "pass_rate_change": after - before if before is not None and after is not None
                                else None}
    write_rows(args.output + ".cases.jsonl", (grade for _, _, grade in results))
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    chart_path = Path(args.output).with_suffix(".svg")
    if chart_path == Path(args.output):
        chart_path = chart_path.with_suffix(".chart.svg")
    write_chart(report, chart_path)
    log.info("Wrote scorecard to %s and per-case grades to %s.cases.jsonl", args.output, args.output)
    return 0 if report["overall"]["complete"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, ImportError) as exc:
        print(f"Evaluation configuration or input error ({type(exc).__name__}): {exc}", file=sys.stderr)
        sys.exit(1)
