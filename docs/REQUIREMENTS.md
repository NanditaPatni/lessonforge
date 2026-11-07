# Requirements traceability: assignment brief → implementation → proof

Every requirement in the brief maps to the code that implements it and to an automated test that proves it.
Run all the acceptance tests offline (no API keys needed):

```bash
pytest tests/test_requirements.py -v
```

## 1. AI-powered lesson generation

| Brief requirement | Implementation | Test |
|---|---|---|
| Accept Lesson Title and Learning Objective as inputs | `POST /api/lessons` (`app/main.py`), UI form, CLI `app/cli.py` | `test_1_accept_lesson_title_and_learning_objective_as_inputs` |
| Research books, websites and YouTube videos | `app/research/web.py` (Tavily/DuckDuckGo + trafilatura), `books.py` (Google Books → Open Library), `youtube.py` (YouTube API/DDG + transcripts), orchestrated by `collector.py` | `test_1_research_books_websites_and_youtube` |
| Structured, engaging content with explanations, examples and practical applications | `app/pipeline/generator.py`: hook → concepts → worked examples → hands-on activity → summary, cited `[S#]` | `test_1_structured_content_with_explanations_examples_practical_applications` |
| Include relevant YouTube videos as supplementary resources | Videos selected by transcript relevance; "Supplementary videos" section | `test_1_youtube_videos_included_as_supplementary_resources` |
| Include reference links at the end of the lesson | References built in code from retrieved sources only (no model-written URLs) — `app/export.py` | `test_1_reference_links_at_end_of_lesson` |
| Completable within 45 minutes incl. explanations, examples, activities, knowledge checks | Deterministic timing model (`lesson_model.compute_timing`) for self-paced **and** instructor-led; budget-driven word targets | `test_1_completable_within_45_minutes_...` |

## 2. Two quality-control layers

| Brief requirement | Implementation | Test |
|---|---|---|
| QC1: verify factual correctness; identify unsupported or misleading claims | `qc_research.py`: atomic claim extraction, per-claim evidence retrieval, two signals (evidence verdict + expert check), independent second-opinion web search | `test_2_qc1_verifies_facts_...` |
| QC1: check reliability and relevance of sources | Rule-based credibility tiers with reasons (`credibility.py`), dead-link check, citation counts, low-tier sources flagged | `test_2_qc1_checks_reliability_and_relevance_of_sources` |
| QC1: identify gaps or inconsistencies | Consistency audit (contradictions, misleading simplifications, wrong quiz keys, factual gaps) | `test_2_qc1_identifies_gaps_or_inconsistencies` |
| QC2: alignment with the learning objective | Traceability matrix (taught / practised / assessed per sub-objective), objective key-term coverage, Bloom alignment | `test_2_qc2_checks_objective_alignment_...` |
| QC2: clarity, structure and relevance | Rubric review (8 criteria) + readability, section length, structure checks | same |
| QC2: gaps in explanations, examples or practical applications | Rubric issues + automated "no example / no activity" checks | same |
| Both layers generate separate audit reports with issues and suggested improvements | Two reports in the UI and `qc1_research_report.md` / `qc2_instruction_report.md` in the export | `test_2_both_layers_produce_separate_audit_reports_...` |
| Two *independent* layers | Separate prompts; QC never sees the generator's prompt; QC can run on a different provider (`QC_LLM_PROVIDER`, set automatically by the doctor) | `tests/test_doctor.py` |

## 3. Review, edit and export

| Brief requirement | Implementation | Test |
|---|---|---|
| Review, edit and save lesson content | Section/quiz/video editor; each save is a new version | `test_3_instructors_can_review_edit_and_save` |
| Review and apply QC recommendations | Per-issue Apply (exact text fix or targeted AI edit) or Dismiss buttons; versions + diff (History) | `test_3_instructors_can_review_and_apply_qc_recommendations` |
| Preview of the final lesson | `GET /api/lessons/{id}/preview` (rendered HTML) | `test_3_preview_of_final_lesson` |
| Export finalized lesson, references and QC reports | ZIP: lesson.md/.html/.json, references.md, both QC reports, qc_reports.json, version history | `test_3_export_finalized_lesson_references_and_qc_reports` |

## 4. Error handling

| Brief requirement | Implementation | Test |
|---|---|---|
| Handle empty inputs | Client- and server-side validation (HTTP 422) | `test_4_empty_inputs_are_rejected` (4 cases) |
| Handle research or AI generation failures | Per-step errors shown in UI, automatic retry once, model fallback chain, daily-quota detection, partial QC reports, time limits on web calls | `test_4_research_or_generation_failure_is_reported_and_retryable`, `tests/test_llm.py`, `tests/test_chaos.py` |
| Preserve previously saved work and instructor edits | Append-only versions, 409 on stale saves, browser draft backup, regenerate never overwrites | `test_4_saved_work_and_instructor_edits_are_preserved` |
| Allow failed steps to be retried | Retry from the failed step; earlier step outputs reused | `test_4_research_or_generation_failure_is_reported_and_retryable` |

## Sample inputs

| Brief requirement | Implementation | Test |
|---|---|---|
| HTTP Requests and Responses + one other topic | "HTTP" and "SQL Joins" sample buttons | `test_sample_input_http_and_one_other_topic` |
| Verify factual accuracy | QC1 + manual verification checklist in `docs/DEMO_SCRIPT.md` | — (manual step, shown in demo video) |

## Submission

| Brief requirement | Where |
|---|---|
| Source code + basic setup instructions | This repository; `README.md` → Quick start; `setup.bat` |
| Demo video | Script: `docs/DEMO_SCRIPT.md` |
| Acknowledge external resources | `README.md` → Acknowledgements |
| Be able to explain implementation | `docs/DESIGN.md` (decisions & trade-offs) |
