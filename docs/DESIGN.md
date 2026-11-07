# Design decisions and trade-offs

## 1. Pipeline as a resumable state machine
`plan → research → generate → qc_research → qc_instruction`. Each step reads its inputs from the
database and writes its output back.

* **Why:** LLM and research calls fail (rate limits, timeouts, flaky sites). Retrying *from the failed
  step* is cheaper and keeps earlier results.
* A server crash leaves steps marked `running`. On startup these are converted to `failed` so they can be retried.
* The pipeline runs in a background thread and the UI polls. A task queue (Celery/RQ) is not needed for a
  locally running tool, and the step and DB design would carry over to one unchanged.

## 2. Grounding: the model never writes URLs
Research produces an **evidence store**: each source is chunked into ~130-word passages (`S2.p3`). The generator
receives the most relevant passages for each sub-objective and must cite source IDs (`[S2]`).
`normalize_lesson()` then removes any citation to a source that does not exist. The reference list is assembled
from the retrieved sources. **A fabricated link cannot reach the lesson.**

**Why BM25 and not embeddings?** Each lesson's corpus is a few hundred passages. BM25 is transparent (you can
see why a passage matched), deterministic, needs no extra model or API, and handles exact technical tokens
like `5xx` or `201` well. Embeddings can blur those tokens together. Retrieval scores are weighted by source
credibility.

## 3. Source credibility is rule-based on purpose
Domains are sorted into tiers (authoritative / reputable / general / low) using curated lists: standards
bodies, official docs, .gov/.edu, known publishers, educational channels. Every tier comes with a human-readable
reason. An LLM judging credibility would be opaque and could vary between runs. Here an instructor can see and
challenge exactly why a source was trusted.

Selection mixes relevance (coverage of objective terms) and credibility. It allows at most 2 pages per domain
so one site cannot dominate.

## 4. Time budget is computed, not asked for
The LLM is told the budget, but the budget is **measured** in code:

| Component | Self-paced | Instructor-led |
|---|---|---|
| Prose | words / 180 wpm | words / 130 wpm × 1.15 (questions, discussion) |
| Code / raw messages | 0.10 min per line | 0.15 min per line |
| Activities | declared `activity_minutes` | same |
| Quiz | 1.0 min per question | 1.5 min per question (includes going over answers) |
| Videos | only `in_lesson` videos, real duration | same |

The lesson must fit **both** delivery modes, because the brief says "self-learning or instructor-led". If the draft
misses the budget, one resize pass runs. QC2 reports any remaining deviation. All speeds are configurable.

## 5. QC Layer 1: research and factual accuracy
1. **Citation integrity (deterministic):** concept/example sections without citations, more than 60% of citations
   pointing to one source, low-tier sources, dead links (HEAD request, with GET fallback for sites that block HEAD).
2. **Claim extraction:** atomic, checkable claims with a *verbatim quote*, so a fix can target the exact text.
   Quiz answer keys count as claims.
3. **Per-claim retrieval:** each claim gets its own top-3 evidence passages, not the generator's evidence pack.
4. **Two independent signals per claim:**
   * `verdict`: judged *only* against the evidence (supported / partially / unsupported / contradicted)
   * `knowledge_check`: expert knowledge (correct / incorrect / uncertain)

   | verdict | knowledge | severity | meaning |
   |---|---|---|---|
   | contradicted or any | incorrect | critical | factual error |
   | unsupported | uncertain | major | could be wrong, no evidence |
   | unsupported | correct | minor | true but needs a source |
   | partially | — | minor/major | imprecise or overstated |
5. **Second-opinion retrieval:** unsupported or contradicted claims trigger a fresh web search. The generator
   never saw these results. The claim is re-verified against the new source. A supported result produces an
   `add_citation` fix that registers the new source. A contradiction is confirmed as an error.
6. **Holistic audit:** internal contradictions, misleading simplifications, wrong quiz keys, important gaps.

## 6. QC Layer 2: instructional quality
Deterministic checks run first because they are reproducible and an LLM cannot argue its way past them:
* **Traceability matrix:** every sub-objective must be taught, practised (example/activity) and assessed (quiz).
  This is constructive alignment (Biggs) made checkable.
* **Objective key-term coverage:** every content word of the objective must appear in the lesson body.
* **Bloom alignment:** a "distinguish/compare/differentiate" objective needs a comparison table or a
  classification task, not only explanation.
* **Quiz hygiene:** answer-position bias, correct option noticeably the longest, "all of the above", duplicate options.
* **Cognitive load:** sections over 450 words; Flesch reading ease below 25.
* **Structure:** hook first, summary last, an example and an activity are both present.

Then an independent rubric review (8 criteria, 1–5 each) receives the automated findings as context. It is told
not to repeat them and to judge the qualitative parts: clarity, quality of examples, engagement.

## 7. Applying fixes: smallest possible change
Fix operations, in order of preference:
* `replace_text`: exact string replacement, no LLM involved, nothing else changes.
* `add_citation`: registers the independently found source and appends `[S#]` after the quote.
* `rewrite_section` / `rewrite_quiz` / `add_section`: an LLM edit limited to **one section or the quiz only**,
  given relevant evidence and told to keep citations valid.
* `manual`: source-level issues (dead link, single-source reliance) that need human judgement.

Every application creates a new version. Each fix in a batch is applied independently: one failure does not block the others.
After applying, the QC reports show "audited v1, latest is v3" with a re-run button, so improvement is
**measured** rather than assumed.

## 8. Protecting instructor work
* Versions are append-only (`ai`, `instructor`, `qc-apply` authors). Restoring a version copies it forward instead of deleting newer ones.
* Saving sends `base_version`. If someone else (or a QC apply) created a newer version, the server returns
  **409** instead of silently overwriting it.
* Unsaved edits are mirrored to `localStorage`. After a crash or refresh the UI offers to restore the draft.
* Polling never re-renders the editor while you type.
* Input validation: blank or too-short title/objective is rejected on both client and server.

## 9. Robustness of LLM output
* JSON mode where the provider supports it, plus a tolerant parser (code fences, leading prose).
* Every response passes a validator. On failure the model is re-asked with the reason (up to 3 attempts).
* Retries with exponential backoff and jitter on 408/429/5xx; `Retry-After` is respected.
* Clear errors for a missing key (401/403) and a wrong model name (404).
* `normalize_lesson()` repairs common drift: objectives given as a string, answer given as "B", unknown section types.

## 10. Reliability on free-tier models (lessons from real runs)
Free models fail in predictable ways, and each one has a specific defence:

| Failure seen in practice | Defence |
|---|---|
| Retired model name (404), overloaded model (503) | `app/doctor.py` probes models, writes an ordered chain; `FALLBACK_LLM` + sticky fallback in `llm.py` |
| Request too large / output cut off (413, truncation) | chunked generation (outline, then sections in batches of 3); compact evidence for low-limit providers |
| Odd but valid JSON: bare lists, extra wrapper objects, `content` instead of `content_md`, quiz options as a dict, answer as `"C"` or option text, scores as `"4/5"` | `app/shapes.py` normalises every reply before validation |
| Model writes passage IDs `[S6.p8]` instead of source IDs | citations are normalised to `[S6]` on every save |
| A transient error in the middle of a step | the step retries automatically once after 20 s, then shows **Retry** |
| One QC sub-check fails (e.g. rubric reviewer busy) | QC returns a **partial report** with a visible warning instead of failing the whole step |
| A section the model skipped while writing in batches | that section is requested on its own |

`tests/test_chaos.py` sends **120 randomly malformed reply sequences** through the whole pipeline (generation, both QC
layers, then applying every recommendation) and asserts that nothing crashes. With `shapes.py` disabled, the test fails.

## 11. What I would do next
* Store each source's full text and let QC1 quote exact sentences in the report (provenance highlighting).
* Use embeddings plus BM25 hybrid retrieval when lessons draw on long documents.
* Evaluate on a labelled set of lessons with planted errors to measure QC1 precision and recall.
* Add multi-user auth and comments for team review.
