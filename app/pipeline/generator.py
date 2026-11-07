"""Step 3 — evidence-grounded lesson generation with a deterministic time-budget loop."""
from __future__ import annotations

import json
from typing import Callable

from .. import prompts
from ..config import get_settings
from ..llm import LLMClient
from .evidence import EvidenceIndex, fmt_passages, fmt_sources
from .lesson_model import compute_timing, normalize_lesson


def budget_targets(minutes: int) -> dict:
    f = minutes / 45
    return {"min_words": int(1300 * f), "max_words": int(1900 * f), "act_min": int(10 * f), "act_max": int(16 * f),
            "q_min": 5, "q_max": 8}


def _validate_raw(d: dict) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("sections"), list) or len(d["sections"]) < 3:
        raise ValueError("lesson must contain at least 3 sections")
    if not isinstance(d.get("quiz"), list) or len(d["quiz"]) < 3:
        raise ValueError("lesson must contain a quiz with at least 3 questions")


# Sized so a typical 8-9 section lesson lands at ~30-35 min self-paced and ~40-44 min instructor-led.
WORD_TARGET = {"hook": 150, "concept": 330, "example": 260, "activity": 200, "summary": 120}


def _validate_outline(d: dict) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("sections"), list) or len(d["sections"]) < 3:
        raise ValueError("outline must contain at least 3 sections")
    if not isinstance(d.get("quiz"), list) or len(d["quiz"]) < 3:
        raise ValueError("outline must contain a quiz with at least 3 questions")


def _validate_batch(d: dict) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("sections"), list) or not d["sections"]:
        raise ValueError("'sections' list missing")


def _generate_chunked(title, objective, plan, research, llm, idx, evidence, text_sources, videos, emit) -> dict:
    """Low-TPM path: one call for outline + quiz, then section content in batches of 3.
    Each request stays well under free-tier tokens-per-minute limits."""
    s = get_settings()
    minutes = s.lesson_minutes
    los = "\n".join(f"- {lo['id']} ({lo.get('bloom', '')}): {lo['text']}" for lo in plan["sub_objectives"])
    outline = llm.json(
        prompts.GENERATE_SYSTEM.format(minutes=minutes),
        prompts.OUTLINE_USER.format(
            title=title, objective=objective, audience=plan.get("audience", ""), sub_objectives=los,
            misconceptions="; ".join(plan.get("common_misconceptions", [])) or "none listed",
            sources=fmt_sources(text_sources), evidence=fmt_passages(evidence[:10], 70),
            videos=fmt_sources(videos) or "(none found)", minutes=minutes, **budget_targets(minutes)),
        task="outline", context={"title": title, "objective": objective, "plan": plan, "research": research},
        validate=_validate_outline, temperature=0.4)
    secs = [x for x in outline["sections"] if isinstance(x, dict)]
    for i, x in enumerate(secs):
        x["id"] = str(x.get("id") or f"sec-{i + 1}")
    emit(f"Outline ready: {len(secs)} sections, {len(outline['quiz'])} quiz questions — writing sections in batches")
    outline_txt = "\n".join(f"[{x['id']}] ({x.get('type')}) {x.get('heading')}: {x.get('brief', '')}" for x in secs)
    written: dict[str, str] = {}
    for b in range(0, len(secs), 3):
        batch = secs[b:b + 3]
        targets = "\n".join(
            f"- [{x['id']}] ({x.get('type')}{', activity ' + str(x.get('activity_minutes')) + ' min' if x.get('activity_minutes') else ''}) "
            f"{x.get('heading')} — about {WORD_TARGET.get(str(x.get('type')), 200)} words. Must cover: {x.get('brief', '')}"
            for x in batch)
        ev = idx.pack([f"{x.get('heading', '')} {x.get('brief', '')}" for x in batch], per_query=3, cap=8)
        data = llm.json(
            prompts.GENERATE_SYSTEM.format(minutes=minutes),
            prompts.SECTIONS_USER.format(title=title, objective=objective, audience=plan.get("audience", ""),
                                         outline=outline_txt, sources=fmt_sources(text_sources),
                                         evidence=fmt_passages(ev, 80), targets=targets, minutes=minutes),
            task="sections", context={"batch": batch, "title": title, "research": research},
            validate=_validate_batch, temperature=0.4)
        for item in data["sections"]:
            if isinstance(item, dict) and item.get("content_md"):
                written[str(item.get("id"))] = str(item["content_md"])
        emit(f"Wrote sections {b + 1}-{b + len(batch)} of {len(secs)}")
    # Sections the model skipped or mis-labelled: ask for each one on its own (small, reliable requests).
    for x in [x for x in secs if x["id"] not in written]:
        try:
            ev = idx.pack([f"{x.get('heading', '')} {x.get('brief', '')}"], per_query=4, cap=6)
            data = llm.json(
                prompts.GENERATE_SYSTEM.format(minutes=minutes),
                prompts.SECTIONS_USER.format(
                    title=title, objective=objective, audience=plan.get("audience", ""), outline=outline_txt,
                    sources=fmt_sources(text_sources), evidence=fmt_passages(ev, 80), minutes=minutes,
                    targets=f"- [{x['id']}] ({x.get('type')}) {x.get('heading')} — about {WORD_TARGET.get(str(x.get('type')), 200)} words. "
                            f"Must cover: {x.get('brief', '')}"),
                task="sections", context={"batch": [x], "title": title, "research": research},
                validate=_validate_batch, temperature=0.4)
            items = [i for i in data["sections"] if isinstance(i, dict) and i.get("content_md")]
            if items:   # accept the reply even if the model changed the id
                written[x["id"]] = str(items[0]["content_md"])
                emit(f"Re-wrote missing section {x['id']}")
        except Exception as e:  # noqa: BLE001
            emit(f"Could not write section {x['id']}: {str(e)[:120]}")
    missing = [x["id"] for x in secs if x["id"] not in written]
    if len(missing) > len(secs) // 3:
        raise ValueError(f"model did not write sections {missing}; click Retry")
    secs = [x for x in secs if x["id"] in written]   # drop a stray unwritten section rather than fail the lesson
    for x in secs:
        x["content_md"] = written[x["id"]]
        x.pop("brief", None)
    outline["sections"] = secs
    return outline


def generate(title: str, objective: str, plan: dict, research: dict, llm: LLMClient,
             emit: Callable[[str], None] = lambda m: None) -> tuple[dict, dict, list[str]]:
    s = get_settings()
    minutes = s.lesson_minutes
    idx = EvidenceIndex(research)
    queries = [lo["text"] for lo in plan["sub_objectives"]] + plan.get("key_concepts", [])[:6] + \
              plan.get("common_misconceptions", [])[:3]
    evidence = idx.pack(queries, per_query=3, cap=14) if s.compact else idx.pack(queries)
    passage_words = 80 if s.compact else 120
    text_sources = [x for x in research["sources"] if x["type"] != "video"]
    videos = [x for x in research["sources"] if x["type"] == "video"]
    emit(f"Generating with {len(evidence)} evidence passages from {len(text_sources)} sources"
         + (" (compact mode for low token limits)" if s.compact else ""))

    user = prompts.GENERATE_USER.format(
        title=title, objective=objective,
        audience=plan.get("audience", ""), prerequisites=", ".join(plan.get("prerequisites", [])) or "none",
        sub_objectives="\n".join(f"- {lo['id']} ({lo.get('bloom', '')}): {lo['text']}" for lo in plan["sub_objectives"]),
        misconceptions="\n".join(f"- {m}" for m in plan.get("common_misconceptions", [])) or "- (none listed)",
        sources=fmt_sources(text_sources), evidence=fmt_passages(evidence, passage_words),
        videos=fmt_sources(videos) or "(none found)", minutes=minutes, **budget_targets(minutes),
    )
    if s.chunked or s.compact:
        raw = _generate_chunked(title, objective, plan, research, llm, idx, evidence, text_sources, videos, emit)
    else:
        raw = llm.json(prompts.GENERATE_SYSTEM.format(minutes=minutes), user, task="generate",
                       context={"title": title, "objective": objective, "plan": plan, "research": research},
                       validate=_validate_raw, temperature=0.4)
    raw.setdefault("sub_objectives", plan["sub_objectives"])
    lesson, notes = normalize_lesson(raw, research["sources"], plan)
    timing = compute_timing(lesson, research["sources"])
    emit(f"Draft timing: self-paced {timing['self_paced']} min, instructor-led {timing['instructor_led']} min")

    # One resize pass if the draft misses the budget; QC2 reports any remaining deviation.
    # (Skipped in compact mode: resending the whole lesson would exceed low per-minute token limits.)
    if timing["status"] != "ok" and not (s.compact or s.chunked):
        direction = ("Shorten it: trim explanatory text and/or activity time so BOTH timings are at most "
                     f"{minutes} minutes.") if timing["status"] == "over" else (
                     f"Expand it: deepen explanations/examples so self-paced time is at least {int(minutes * 0.75)} minutes, "
                     f"without exceeding {minutes} minutes instructor-led.")
        try:
            resized = llm.json(
                prompts.FIT_BUDGET_SYSTEM,
                prompts.FIT_BUDGET_USER.format(
                    minutes=minutes, self_paced=timing["self_paced"], instructor_led=timing["instructor_led"],
                    breakdown=json.dumps([{k: r[k] for k in ("section_id", "self_paced", "instructor_led")} for r in timing["rows"]]),
                    direction=direction, lesson=json.dumps(lesson)),
                task="fit_budget", context={"lesson": lesson}, validate=_validate_raw)
            resized.setdefault("sub_objectives", plan["sub_objectives"])
            resized_lesson, n2 = normalize_lesson(resized, research["sources"], plan)
            t2 = compute_timing(resized_lesson, research["sources"])
            if abs(max(t2["self_paced"], t2["instructor_led"]) - minutes) < abs(max(timing["self_paced"], timing["instructor_led"]) - minutes):
                lesson, timing, notes = resized_lesson, t2, notes + n2 + ["Resized to fit the time budget"]
                emit(f"Resized: self-paced {t2['self_paced']} min, instructor-led {t2['instructor_led']} min")
        except Exception as e:  # resizing is best-effort; QC2 will flag the budget
            notes.append(f"Budget resize pass failed: {e}")
    lesson["title"] = lesson["title"] or title
    lesson["objective"] = objective  # the instructor's objective is authoritative
    if llm.fallback_used:
        notes.append(f"Some calls used the fallback model ({llm.label})")
    return lesson, timing, notes
