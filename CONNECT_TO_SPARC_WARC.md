# Connect the news RAG evaluator to sparc_warc

Start with [how the evaluation works](NEWS_RAG_EVALUATION.md) for the workflow, metrics, and information needed from the team.

This guide is for the team integrating the standalone `news-eval` runner into [sparc_warc](https://github.com/Marwan051/sparc_warc). The runner drafts questions from archived articles, calls the RAG system over HTTP, and grades the final answer against the evidence sent to the generator. The repository’s [RAG API plan](https://github.com/Marwan051/sparc_warc/blob/master/docs/rag-api-plan.md) describes the intended interface. Confirm the deployed contract before wiring staging.

## 1. Add the runner in a branch

From the machine that has both the team repository and the evaluator:

```bash
git clone https://github.com/Marwan051/sparc_warc.git
cd sparc_warc
git switch -c eval/news-rag
mkdir -p evaluation/news_rag
cp -R "/home/rober/Desktop/bibilotheca intern/RAG Eval/news-eval/"{README.md,rag_eval.py,smoke_test.py,requirements.txt,config.example.json,sample.sql,.gitignore,news_eval,tests} evaluation/news_rag/
```

If the repo is already cloned, enter that checkout and start from the branch your team uses for integration. Adjust the source path if the evaluator was transferred to another machine. Keep the evaluator's Python dependencies separate from the Spark ingestion environment.

```bash
cd evaluation/news_rag
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python smoke_test.py
python -m unittest discover -v
```

The smoke test uses three fictional articles and simulated HTTP responses. Expect two passes and one deliberate failure. Use `python smoke_test.py --output-dir runs/smoke-results` to keep `pipeline.log`, `scorecard.json`, and the graph `scorecard.svg`.

## 2. Connect the archive snapshot

Set `RAG_EVAL_PG_DSN` locally to a connection string for a read-only PostgreSQL account on a frozen snapshot or restored copy. The runner's `sample.sql` joins `pages.id` to `metadata.page_id` and `content.page_id`, and uses `websites.domain` to diversify the publisher sample. It reads publication time from `metadata.published_date` and capture time from `pages.warc_date`. Set the page ID range for the archive slice you want to sample. [Archive schema](https://github.com/Marwan051/sparc_warc/blob/master/db/db_handler.py#L1215-L1253)

The query produces **article-level** references such as `article:p10`. These are suitable for drafting and reviewing questions. To measure chunk Hit@k and Recall@k, export passage rows from the same chunking version used by staging and pass them with `generate --articles chunks.jsonl`. Each row must include `source_id` (the ID staging reports), `source_unit: "chunk"`, `event_id`, `language`, and `text`. Include publication and capture timestamps where available. The repo documents `article_chunks` and `chunk_embeddings` as versioned tables. Verify their deployed keys and version columns before writing the chunk exporter. [Storage architecture](https://github.com/Marwan051/sparc_warc/blob/master/docs/architecture.md#storage-and-deployment)

The article query initially uses page IDs as event IDs. Group articles that cover the same event before freezing the benchmark, so translations and related stories stay in the same split. For questions that require multiple articles, set a shared event ID in a JSONL sample before drafting.

## 3. Connect staging and the model provider

Get these details from whoever operates staging:

| Needed | Where it goes |
|---|---|
| Deployed ask URL and whether it accepts filters | `staging.url`, optional `staging.question_field` and `staging.filters_field` |
| Redacted answer and context payloads from the same request | `staging.response_fields`, `staging.trace_url`, and `staging.trace_fields`, or an inline adapter |
| Archive identity and deployed build ID | `staging.snapshot` and `staging.build` |
| Approved chat-compatible provider and drafting/judging model IDs | `llm.base_url`, then `--model` on the respective commands |
| Required secrets | `RAG_EVAL_PG_DSN`, `RAG_EVAL_STAGING_TOKEN`, `RAG_EVAL_TRACE_TOKEN`, and `RAG_EVAL_LLM_TOKEN` as needed for your deployment |

Copy `config.example.json` to an untracked `config.json`. Set the staging URL and the real snapshot and build identifiers. Set `llm.base_url` to the team's approved OpenAI-compatible provider, including its API prefix (usually `/v1`). Supply credentials through the environment variables named in the config. Keep the DSN, tokens, run traces, and private datasets out of commits.

The evaluator expects an answer, displayed citations, and a request ID from the question endpoint. Its current adapter obtains the exact generator context from a separate authenticated trace URL keyed by that request ID. The repo's planned `POST /v1/ask` response includes an inline `evidence` array. Choose the integration that matches the deployed service. [Planned ask contract](https://github.com/Marwan051/sparc_warc/blob/master/docs/rag-api-plan.md#post-v1ask)

- **Separate staging trace.** Set `staging.trace_url` to the authorized trace route and configure the field names. It must return the same request ID and the passages actually sent to the generator.
- **Inline evidence.** Update `run_case()` in `news_eval/staging_runner.py` to read the evidence from the ask response and normalize it into the same ordered passage list. The current evaluator requires this adapter change for inline evidence. Verify that the array contains the context actually used for the final answer. Preserve stable chunk IDs when evidence merges adjacent passages, and retain timestamps and provenance used for judging.
- **CLI-only deployment.** You can already sample articles and draft questions. To run this evaluator end to end, the team still needs an HTTP staging endpoint or a separate adapter around the existing Python agent.

For each passage, the runner expects a string `source_id` and `text`. Dated cases also need the applicable `published_at` or `captured_at`. Align the ID granularity used in traces and labels before interpreting retrieval scores.

## 4. Run the pilot and open a PR

Run these blocks **in order** from `evaluation/news_rag` with your environment configured. Use a fresh run directory and substitute your snapshot label, page range, and model IDs.

**Draft questions**

```bash
mkdir -p runs/pilot-v1
python rag_eval.py --config config.json --log-file runs/pilot-v1/pipeline.log generate \
  --sql sample.sql --min-page-id 1 --max-page-id 10000 \
  --snapshot SNAPSHOT_ID --version pilot-v1 --languages ar en fr \
  --model DRAFT_MODEL --output runs/pilot-v1/drafts.jsonl \
  --sample-output runs/pilot-v1/review-sources.jsonl
```

**Review and run.** Have fluent reviewers check the draft questions and evidence. Save accepted cases to `runs/pilot-v1/reviewed.jsonl` with `review_status` set to `reviewed`. Add verified abstention cases and freeze the event split. For an initial connection check, run a small pilot first and verify one staging request ID, its citations, and its context against the trace.

```bash
python rag_eval.py --config config.json --log-file runs/pilot-v1/pipeline.log run \
  --dataset runs/pilot-v1/reviewed.jsonl --output runs/pilot-v1/staging-runs.jsonl
```

**Grade and report.** Supply human grades wherever the judge has not been validated. Each human grade needs the case ID, case digest, build, and request ID from the run, plus a reason and the six boolean criteria described in the evaluator README. With no human grades or previously validated judge cases in the same scoring input, expect the release report to be incomplete.

```bash
python rag_eval.py --config config.json --log-file runs/pilot-v1/pipeline.log score \
  --dataset runs/pilot-v1/reviewed.jsonl --runs runs/pilot-v1/staging-runs.jsonl \
  --humans runs/pilot-v1/human-grades.jsonl --model JUDGE_MODEL \
  --output runs/pilot-v1/scorecard.json
```

Open `scorecard.svg` for the graph, `scorecard.json` for the numbers, and `scorecard.json.cases.jsonl` for individual grading reasons. All three are in `runs/pilot-v1/`. Score exit code `2` means the report is incomplete, commonly because of missing context traces or unresolved grades. Use `score --mode development` to score tuning cases while checking the integration.

In the PR, include the evaluator and any staging adapter, the offline smoke result, and which live checks are pending. GitHub CI can run the smoke test without credentials. Keep the live pilot opt-in and run it where the read-only snapshot and staging service are reachable. The first live scorecard establishes the baseline. Use the same reviewed cases on the next release candidate.

From the repository root, stage the source files explicitly and review the diff before publishing:

```bash
git add evaluation/news_rag/{README.md,rag_eval.py,smoke_test.py,requirements.txt,config.example.json,sample.sql,.gitignore,news_eval,tests}
git diff --cached --check
git diff --cached --stat
git commit -m "Add multilingual news RAG evaluator"
git push -u origin eval/news-rag
```

Open a pull request from `eval/news-rag` to your team's integration branch. If you use a fork, push the branch to the fork and select the team repository as the PR base.
