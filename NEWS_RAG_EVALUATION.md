# How we evaluate the news RAG system

The release question is **how often can a user ask the news archive a question and receive a correct answer supported by the archive?** When the archive lacks evidence, the expected behavior is to say so. The evaluator measures those outcomes by calling the same staging RAG endpoint a user would call.

## Follow one question through the evaluation

| Step | What happens | Output |
|---|---|---|
| 1. Sample | Read a bounded sample of Arabic, English, and French articles from a fixed PostgreSQL snapshot. Record publication and WARC capture dates separately. | Source texts and metadata |
| 2. Draft | A configurable LLM proposes questions, required facts, and exact supporting quotes. Python checks that the quotes occur in the source. | Draft test cases |
| 3. Review | Fluent reviewers verify facts and wording, add confirmed coverage gaps, and group translations and related articles by news event. Keep entire events in either tuning or holdout. | Frozen, reviewed test set |
| 4. Run staging | Send the question and any user-visible filters. Your RAG pipeline retrieves and generates as usual. Capture the final answer, citations, latency, and exact context sent to the generator. | One run record per case |
| 5. Grade and compare | Grade the response against the reviewed facts and the actual context. Use human grades for release categories where the judge has not been validated. Compare builds on the same test set. | Per-case grades and a scorecard |

For example, take an Arabic question asking when a ferry reopened **as of Tuesday**. The reference case says Monday and points to an English article from Tuesday. If staging answers “Monday” in Arabic but its only supporting article was published on Wednesday, the case fails the date requirement. The response can be factually correct and still fail the overall case.

## What the metrics mean

| Metric | What we count |
|---|---|
| **End-to-end pass rate** | Passed cases divided by all cases in a complete scorecard. An answerable case passes when it includes the required facts, supports material claims with archive evidence, and meets applicable citation, date, and language requirements. An unanswerable case passes when it acknowledges insufficient evidence without guessing. HTTP errors and timeouts count as failures. |
| **Unsupported answers** | Count of graded responses with a material claim that the context does not support. |
| **Incorrect refusals** | Count of answerable cases where the system declined to answer. |
| **Hit@k / Recall@k** | Among cases with matching chunk labels and a valid trace, Hit@k asks whether any labeled chunk appeared in the first k context passages. Recall@k measures the average fraction of labeled chunks found there. Finding one of two labeled chunks is a hit and gives 50% recall for that case. |
| **p95 latency** | The time within which 95% of measured staging requests completed or failed. It excludes the later trace fetch and judge call. |

Report counts and pass rates by query language, query-to-evidence language pair, and question type. Correctness and faithfulness are distinct checks in the per-case grades. Missing traces or unresolved grades appear as **unscored** and make the scorecard incomplete. The current sampler emits article IDs, so chunk retrieval scores require labels from the deployed chunking version.

## What we need from the team

| Needed | Specific information |
|---|---|
| **Staging contract** | Is there a deployed ask endpoint? Share the request shape and a redacted response with citations and request ID. Confirm whether evidence is inline or retrieved separately, and whether it is the exact context sent to the final generator. |
| **Archive snapshot and deployed index** | Identify the frozen snapshot or restored copy we should sample and how to access it read-only. For retrieval metrics, provide the staging chunk ID format, chunking version, and embedding model/version. The base article schema is already mapped. |
| **Expected user behavior** | Confirm which languages and cross-language pairs we support, the expected answer language, when citations are required, and how to handle partial evidence and date cutoffs. |
| **Evaluation model and reviewers** | Identify the approved provider and model IDs for drafting and judging, and which fluent reviewers can check cases and judge agreement in each priority language. |

**Current status:** The evaluator and article sampler are implemented. The offline smoke test exercised all three commands and caught the planted wrong answer. Its scores came from simulated HTTP responses. The first live baseline still needs the staging contract, archive access, and reviewed cases.

Once those details are confirmed, use [the GitHub integration guide](CONNECT_TO_SPARC_WARC.md) to add the runner to the team repository and run the pilot.

## Which model does what?

| Role | Inputs and outputs | How it affects the evaluation |
|---|---|---|
| **Question drafter** | Receives sampled article excerpts and metadata. Proposes questions, required facts, and verbatim source quotes. | Helps create cases at scale. Python checks the quotes and date bounds. Reviewers check whether questions are natural and whether the facts are supported. |
| **Embedding model in staging** | Converts the staging search query into a vector for PostgreSQL retrieval. | It is part of the system being evaluated. Changes to embeddings can change which passages reach the generator. The evaluator calls staging and uses the model deployed there. |
| **Answer generator in staging** | Receives the user question and retrieved context. Produces the answer and any citations or abstention. | Its output is what the evaluator grades. It receives the same request a user would send. The reference answer and evidence labels stay in the evaluator. |
| **Evaluation judge** | Receives the case rubric, required facts, staging answer and citations, and the actual context. Returns six pass/fail checks and a reason. | Automates grading. Configure it separately from the answer generator. Human grades take precedence, and an unverified judge can help with tuning while release cases still need human grades. |

The drafting and judging roles can use the same provider or separate providers. PostgreSQL sampling, quote validation, ID matching, metric calculations, and chart rendering use Python or SQL. No model call is needed for those steps.

## How a response gets a pass

| Check | What the grader looks for |
|---|---|
| **Facts** | All required facts are present and materially correct. A partial answer fails this check if it omits a required fact. |
| **Groundedness** | The passages actually supplied to the generator support the material claims in its answer. |
| **Citations** | Displayed citations support the claims they accompany. If citations are required, they cover the material claims. |
| **Date** | For a dated question, evidence supporting the answer was published or captured by the relevant cutoff. |
| **Language** | The answer uses the expected response language where specified. |
| **Abstention** | The system answers when evidence supports an answer and acknowledges insufficient evidence when the case calls for it. |

Every applicable check must pass. Inapplicable checks are treated as satisfied. A response may be grounded in a source and still fail correctness if it misstates what the question asked or omits required facts.

For release scoring, the runner verifies automated grading separately for each query-language and question-type pair. The current default requires at least five human-graded, reviewed tuning cases, includes both pass and fail examples, and requires at least 90% exact agreement across the six checks. This is a pilot rule for whether the evaluator may use a judge grade, not a release threshold for the RAG system. A human grade for the specific run takes precedence.

The scorecard also reports **endpoint errors** and **judge errors** as counts. Endpoint errors are failed cases. A judge error can be resolved with a human grade when the trace is available. An incomplete scorecard still displays the counts and the graph, with the pass-rate field marked unavailable.
