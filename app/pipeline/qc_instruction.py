"""QC Layer 2 — Instructional quality.

  A. Deterministic checks (cheap, reproducible, can't be talked out of):
     - objective traceability matrix: every sub-objective must be taught, practised AND assessed
     - objective key-term coverage (e.g. '5xx' must actually appear in the lesson)
     - 45-minute time budget (self-paced and instructor-led)
     - structure (hook -> ... -> summary, worked example, activity present)
     - Bloom alignment (a 'distinguish/compare' objective needs a comparison or classification task)
     - quiz hygiene (answer-position bias, longest-option cue, 'all of the above', duplicates)
     - readability + section length (cognitive load)
  B. Rubric review by an independent model, given the automated findings as context.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable

from .. import prompts
from ..config import get_settings
from ..llm import LLMClient
from ..research.text import flesch_reading_ease, tokenize, word_count
from ..shapes import to_int
from .issues import as_list, as_object, finalize, llm_issue, make_issue
from .lesson_model import compute_timing, lesson_as_text

GENERIC = set("""explain describe identify understand learn learners students able apply compare contrast distinguish
differentiate define list demonstrate analyze analyse evaluate create recognize recognise discuss outline summarize
summarise state know basic different various categories category concept concepts use using between
introduction overview lesson types""".split())
COMPARE_VERBS = ("distinguish", "compare", "contrast", "differentiate", "classify", "categorize", "categorise")
RUBRIC = ["objective_alignment", "clarity", "structure", "examples", "practical_application", "assessment",
          "engagement", "pacing"]


def objective_terms(text: str) -> list[str]:
    return [t for t in dict.fromkeys(tokenize(text)) if t not in GENERIC]


def traceability(lesson: dict) -> list[dict]:
    rows = []
    for lo in lesson.get("sub_objectives", []):
        lid = lo["id"]
        taught = [s["id"] for s in lesson["sections"] if lid in s.get("objectives", []) and s["type"] in ("concept", "hook", "summary")]
        practised = [s["id"] for s in lesson["sections"] if lid in s.get("objectives", []) and s["type"] in ("example", "activity")]
        assessed = [q["id"] for q in lesson.get("quiz", []) if q.get("objective") == lid]
        rows.append({"objective": lid, "text": lo["text"], "taught": taught, "practised": practised, "assessed": assessed,
                     "complete": bool(taught and practised and assessed)})
    return rows


def _auto_checks(lesson: dict, sources: list[dict]) -> tuple[list[dict], dict]:
    s = get_settings()
    issues: list[dict] = []
    secs = lesson["sections"]
    last_concept = next((x["id"] for x in reversed(secs) if x["type"] in ("concept", "example")), secs[-1]["id"])

    # traceability
    matrix = traceability(lesson)
    for row in matrix:
        lid = row["objective"]
        if not row["taught"]:
            issues.append(make_issue("alignment", "critical", last_concept, f"{lid} ('{row['text']}') is never explicitly taught.",
                                     f"Add a concept section that teaches {lid}.",
                                     {"op": "add_section", "instruction": f"Add a concise concept section (objectives: [{lid}]) that teaches: {row['text']}"}))
        if not row["practised"]:
            act = next((x["id"] for x in secs if x["type"] == "activity"), last_concept)
            issues.append(make_issue("practice", "major", act, f"{lid} ('{row['text']}') has no worked example or practice task.",
                                     f"Add an example or activity step that makes learners practise {lid}.",
                                     {"op": "rewrite_section" if act != last_concept else "add_section",
                                      "instruction": f"Add a short practice task for {lid} ('{row['text']}') and tag it with {lid}."}))
        if not row["assessed"]:
            issues.append(make_issue("assessment", "major", "quiz", f"{lid} ('{row['text']}') is not assessed by any quiz question.",
                                     f"Add 1-2 questions that assess {lid}.",
                                     {"op": "rewrite_quiz", "instruction": f"Add 1-2 multiple-choice questions assessing {lid}: {row['text']}. Tag them with objective {lid}."}))

    # key-term coverage
    full_text = " ".join(x["content_md"] for x in secs).lower()
    terms = objective_terms(lesson["objective"])
    missing = [t for t in terms if t not in set(tokenize(full_text))]
    if missing:
        issues.append(make_issue("alignment", "major", last_concept, f"Objective terms never covered in the lesson body: {', '.join(missing)}.",
                                 "Explicitly teach these terms — they are part of the stated objective.",
                                 {"op": "rewrite_section", "instruction": f"Explicitly explain these objective terms with examples: {', '.join(missing)}."}))

    # timing
    timing = compute_timing(lesson, sources)
    if timing["status"] == "over":
        longest = max((r for r in timing["rows"] if r["type"] not in ("quiz", "video")), key=lambda r: r["instructor_led"])
        cut = round(max(timing["self_paced"], timing["instructor_led"]) - timing["budget"], 1)
        issues.append(make_issue("pacing", "major", longest["section_id"],
                                 f"Lesson exceeds the {timing['budget']}-minute budget (self-paced {timing['self_paced']} min, instructor-led {timing['instructor_led']} min).",
                                 f"Cut about {cut} minutes; '{longest['heading']}' is the longest section.",
                                 {"op": "rewrite_section", "instruction": f"Shorten this section to save about {cut} minutes (~{int(cut * s.speaking_wpm)} words or activity time) while keeping key points and citations."}))
    elif timing["status"] == "under":
        issues.append(make_issue("pacing", "minor", last_concept,
                                 f"Lesson is short for a {timing['budget']}-minute slot (self-paced {timing['self_paced']} min).",
                                 "Add a worked example or extend the activity.",
                                 {"op": "add_section", "instruction": "Add a worked example section that deepens understanding of the hardest sub-objective."}))

    # structure
    types = [x["type"] for x in secs]
    if types[0] != "hook":
        issues.append(make_issue("structure", "minor", secs[0]["id"], "Lesson does not open with a hook/motivation.",
                                 "Open with a short real-world scenario or question that motivates the topic.",
                                 {"op": "rewrite_section", "instruction": "Begin this section with a 2-3 sentence real-world hook that motivates the topic."}))
    if types[-1] != "summary":
        issues.append(make_issue("structure", "minor", secs[-1]["id"], "Lesson does not end with a summary.",
                                 "Add a brief summary that maps back to each sub-objective.",
                                 {"op": "add_section", "instruction": "Add a short summary section (type summary) that recaps each sub-objective in one line."}))
    if "activity" not in types:
        issues.append(make_issue("practice", "major", last_concept, "No hands-on activity — learners never apply the knowledge.",
                                 "Add a guided hands-on activity with an expected outcome.",
                                 {"op": "add_section", "instruction": "Add a hands-on activity section (type activity, 8-12 activity_minutes) with steps and expected outcome."}))
    if "example" not in types:
        issues.append(make_issue("examples", "major", last_concept, "No worked example section.",
                                 "Add a worked example that walks through the concept step by step.",
                                 {"op": "add_section", "instruction": "Add a worked example section that walks step by step through a realistic case."}))

    # Bloom alignment for comparison objectives
    if any(v in lesson["objective"].lower() for v in COMPARE_VERBS):
        has_compare = any("|" in x["content_md"] and "---" in x["content_md"] for x in secs) or any(
            x["type"] == "activity" and re.search(r"classif|categor|distinguish|compare|which class|sort", x["content_md"], re.I) for x in secs)
        if not has_compare:
            issues.append(make_issue("alignment", "major", last_concept,
                                     "Objective asks learners to distinguish/compare, but there is no comparison table or classification task.",
                                     "Add a side-by-side comparison table and a classification exercise.",
                                     {"op": "rewrite_section", "instruction": "Add a markdown comparison table contrasting the items the objective asks learners to distinguish."}))

    # quiz hygiene
    quiz = lesson.get("quiz", [])
    if not (5 <= len(quiz) <= 8):
        issues.append(make_issue("assessment", "minor" if len(quiz) >= 3 else "major", "quiz",
                                 f"Knowledge check has {len(quiz)} questions; 5-8 fits a 45-minute lesson.",
                                 "Adjust the number of questions to 5-8.", {"op": "rewrite_quiz", "instruction": "Adjust the knowledge check to 6 questions covering every sub-objective."}))
    if len(quiz) >= 4:
        pos = Counter(q["answer_index"] for q in quiz)
        if pos.most_common(1)[0][1] / len(quiz) >= 0.6:
            issues.append(make_issue("assessment", "minor", "quiz", f"Answer-position bias: {pos.most_common(1)[0][1]} of {len(quiz)} correct answers are option {chr(65 + pos.most_common(1)[0][0])}.",
                                     "Shuffle option order so correct answers are spread across positions.",
                                     {"op": "rewrite_quiz", "instruction": "Reorder options so correct answers are distributed across A-D (update answer_index accordingly)."}))
        longest_cue = sum(1 for q in quiz if len(q["options"][q["answer_index"]]) == max(map(len, q["options"])) and
                          len(q["options"][q["answer_index"]]) > 1.4 * (sorted(map(len, q["options"]))[-2] or 1))
        if longest_cue >= max(2, len(quiz) // 2):
            issues.append(make_issue("assessment", "minor", "quiz", f"In {longest_cue} questions the correct answer is noticeably the longest option (a test-wise cue).",
                                     "Balance option lengths.", {"op": "rewrite_quiz", "instruction": "Rewrite options so the correct answer is not systematically the longest."}))
    for q in quiz:
        low = [o.lower() for o in q["options"]]
        if len(set(low)) < len(low) or any("all of the above" in o or "none of the above" in o for o in low):
            issues.append(make_issue("assessment", "minor", "quiz", f"{q['id']} has duplicate options or 'all/none of the above'.",
                                     "Replace with distinct, plausible distractors.", {"op": "rewrite_quiz", "instruction": f"Fix {q['id']}: use four distinct plausible options, no 'all/none of the above'."}))

    # readability / chunking
    read = []
    for x in secs:
        fre, wc = flesch_reading_ease(x["content_md"]), word_count(x["content_md"])
        read.append({"section_id": x["id"], "flesch": fre, "words": wc})
        if wc > 450:
            issues.append(make_issue("clarity", "minor", x["id"], f"Section '{x['heading']}' is long ({wc} words) — high cognitive load.",
                                     "Split it into smaller chunks with sub-headings or move detail to an example.",
                                     {"op": "rewrite_section", "instruction": "Restructure into shorter chunks with sub-headings and bullet points; trim redundancy."}))
        if wc > 60 and fre < 25:
            issues.append(make_issue("clarity", "minor", x["id"], f"Section '{x['heading']}' is hard to read (Flesch {fre}).",
                                     "Use shorter sentences and plainer words; define jargon on first use.",
                                     {"op": "rewrite_section", "instruction": "Simplify: shorter sentences, plain words, define each technical term on first use. Keep citations."}))
    auto = {"traceability": matrix, "objective_terms": terms, "missing_terms": missing, "timing": timing,
            "readability": read, "structure": types}
    return issues, auto


def run(lesson: dict, sources: list[dict], llm: LLMClient, version: int, emit: Callable[[str], None] = lambda m: None) -> dict:
    minutes = get_settings().lesson_minutes
    issues, auto = _auto_checks(lesson, sources)
    emit(f"Automated instructional checks: {len(issues)} findings")
    auto_summary = "\n".join([
        "Traceability: " + "; ".join(f"{r['objective']} taught={r['taught'] or 'NONE'} practised={r['practised'] or 'NONE'} assessed={r['assessed'] or 'NONE'}" for r in auto["traceability"]),
        f"Timing: self-paced {auto['timing']['self_paced']} min, instructor-led {auto['timing']['instructor_led']} min (budget {minutes})",
        f"Objective terms missing from body: {auto['missing_terms'] or 'none'}",
        "Findings: " + ("; ".join(i["description"] for i in issues) or "none"),
    ])
    warnings: list[str] = []
    try:
        review = llm.json(
            prompts.INSTRUCTION_SYSTEM.format(minutes=minutes),
            prompts.INSTRUCTION_USER.format(
                objective=lesson["objective"], minutes=minutes, auto=auto_summary, lesson=lesson_as_text(lesson),
                sub_objectives="\n".join(f"- {lo['id']}: {lo['text']}" for lo in lesson.get("sub_objectives", []))),
            task="instruction_review", context={"lesson": lesson}, temperature=0.1)
    except Exception as e:  # noqa: BLE001  — the deterministic checks above still give a useful report
        warnings.append(f"Rubric review skipped: {str(e)[:200]}")
        emit("⚠ " + warnings[-1])
        review = {"scores": {}, "issues": [], "strengths": []}
    review = as_object(review, "issues")
    raw_scores = review.get("scores") if isinstance(review.get("scores"), dict) else {}
    scores = {}
    for k in RUBRIC:
        if not raw_scores:
            break                     # rubric unavailable: report only the automated checks
        v = raw_scores.get(k) or {}
        if not isinstance(v, dict):          # e.g. "clarity": 4
            v = {"score": v, "justification": ""}
        sc = max(1, min(5, to_int(v.get("score"), 3)))
        scores[k] = {"score": sc, "justification": str(v.get("justification", ""))}
    for raw in as_list(review.get("issues"))[:12]:
        it = llm_issue(raw, {x["id"] for x in lesson["sections"]}, source="rubric_review")
        if it:
            issues.append(it)
    issues = finalize(issues, "I")
    avg = (sum(v["score"] for v in scores.values()) / len(scores)) if scores else None
    crit = any(i["severity"] == "critical" for i in issues)
    if avg is None:
        status = "fail" if crit else "needs_review"
    else:
        status = "fail" if crit or avg < 3 else ("pass" if avg >= 4 and not any(i["severity"] == "major" for i in issues) else "needs_review")
    return {
        "layer": "instruction",
        "version": version,
        "model": llm.label,
        "status": status,
        "metrics": {"rubric_average": round(avg, 2) if avg is not None else None,
                    "instructional_score": round(avg / 5 * 100) if avg is not None else None,
                    "objectives_fully_covered": sum(r["complete"] for r in auto["traceability"]),
                    "objectives_total": len(auto["traceability"]), "self_paced_minutes": auto["timing"]["self_paced"],
                    "instructor_led_minutes": auto["timing"]["instructor_led"], "budget_minutes": minutes},
        "scores": scores,
        "strengths": [str(x) for x in as_list(review.get("strengths"))][:6],
        "automated": auto,
        "issues": issues,
        "warnings": warnings,
    }
