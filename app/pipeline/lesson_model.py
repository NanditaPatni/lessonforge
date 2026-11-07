"""Lesson schema normalisation + deterministic 45-minute timing model.

The LLM's output is never trusted structurally: everything passes through
normalize_lesson(), which repairs ids/types, strips citations to sources that don't
exist (hallucinated citations), and validates quiz items.
"""
from __future__ import annotations

import re

from ..config import get_settings
from ..research.text import code_lines, word_count
from ..shapes import fix_quiz_item, fix_section, to_float

SECTION_TYPES = ("hook", "concept", "example", "activity", "summary")
# Matches [S1], [S1, S3], and passage-level forms models sometimes write: [S6.p8], [S5.p4, S3.p26]
_ID = r"S\d+(?:\.p\d+)?"
CITE_RE = re.compile(rf"\[({_ID}(?:\s*[,;]\s*{_ID})*)\]")


def _source_id(token: str) -> str:
    """S6.p8 -> S6 (a passage citation counts as a citation of its source)."""
    return token.strip().split(".")[0]


def all_sources(research: dict, lesson: dict | None = None) -> list[dict]:
    """Research sources + any sources added later by QC (independent verification)."""
    extra = (lesson or {}).get("extra_sources") or []
    return list(research.get("sources", [])) + list(extra)


def _fix_citations(text: str, known: set[str], removed: set[str]) -> str:
    def repl(m):
        ids = list(dict.fromkeys(_source_id(x) for x in re.split(r"[,;]", m.group(1))))
        keep = [i for i in ids if i in known]
        removed.update(i for i in ids if i not in known)
        return "".join(f"[{i}]" for i in keep)
    return CITE_RE.sub(repl, text or "")


def _as_list(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[,;]", v) if x.strip()]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _strip_cites(text: str) -> str:
    return re.sub(r"\s{2,}", " ", CITE_RE.sub("", text or "")).strip()


def citations_in(text: str) -> list[str]:
    ids = []
    for m in CITE_RE.finditer(text or ""):
        ids += [_source_id(x) for x in re.split(r"[,;]", m.group(1))]
    return sorted(set(ids), key=lambda s: int(s[1:]))


def normalize_lesson(raw: dict, sources: list[dict], plan: dict | None = None) -> tuple[dict, list[str]]:
    if not isinstance(raw, dict):
        raise ValueError("lesson must be a JSON object")
    notes: list[str] = []
    known = {s["id"] for s in sources}
    video_ids = {s["id"] for s in sources if s.get("type") == "video"}
    removed: set[str] = set()
    plan = plan or {}

    sections, used_ids = [], set()
    raw_sections = raw.get("sections") or []
    if isinstance(raw_sections, dict):
        raw_sections = list(raw_sections.values())
    for i, s in enumerate(raw_sections):
        s = fix_section(s)
        if not isinstance(s, dict):
            continue
        content = str(s.get("content_md") or s.get("content") or "").strip()
        if not content:
            continue
        typ = str(s.get("type") or "concept").lower().strip()
        if typ in ("check", "quiz", "assessment"):
            typ = "activity" if s.get("activity_minutes") else "concept"
        if typ not in SECTION_TYPES:
            typ = "concept"
        sid = re.sub(r"[^a-z0-9-]", "", str(s.get("id") or "").lower()) or f"sec-{i + 1}"
        while sid in used_ids:
            sid += "x"
        used_ids.add(sid)
        act = to_float(s.get("activity_minutes"))
        content = _fix_citations(content, known, removed)
        sections.append({
            "id": sid,
            "type": typ,
            "heading": str(s.get("heading") or f"Section {i + 1}").strip(),
            "objectives": _as_list(s.get("objectives")),
            "activity_minutes": max(0.0, min(act, 30.0)) if typ == "activity" else 0.0,
            "content_md": content,
            "citations": citations_in(content),
        })
    if not sections:
        raise ValueError("lesson has no usable sections")

    quiz = []
    raw_quiz = raw.get("quiz") or []
    if isinstance(raw_quiz, dict):
        raw_quiz = list(raw_quiz.values())
    for i, q in enumerate(raw_quiz):
        q = fix_quiz_item(q)
        if not isinstance(q, dict):
            continue
        opts, ans = q["options"], q["answer_index"]
        if len(opts) < 2 or not (0 <= ans < len(opts)) or not str(q.get("question") or "").strip():
            notes.append(f"Dropped malformed quiz item {q.get('id') or i + 1}")
            continue
        # Citation markers belong in the explanation, never in what learners answer:
        # strip them from the question/options and carry any sources over to the explanation.
        moved = citations_in(" ".join([str(q["question"])] + opts))
        question = _strip_cites(str(q["question"]))
        opts = [_strip_cites(o) for o in opts]
        explanation = _fix_citations(str(q.get("explanation") or ""), known, removed)
        extra = [c for c in moved if c in known and c not in citations_in(explanation)]
        if extra:
            explanation = (explanation.rstrip() + " " + "".join(f"[{c}]" for c in extra)).strip()
        quiz.append({
            "id": f"Q{len(quiz) + 1}",
            "question": question,
            "options": opts,
            "answer_index": ans,
            "explanation": explanation,
            "objective": str(q.get("objective") or ""),
        })

    videos = []
    raw_videos = raw.get("videos") or []
    if isinstance(raw_videos, dict):
        raw_videos = list(raw_videos.values())
    for v in raw_videos:
        if isinstance(v, dict) and v.get("source_id") in video_ids and v["source_id"] not in [x["source_id"] for x in videos]:
            videos.append({
                "source_id": v["source_id"],
                "placement": "in_lesson" if v.get("placement") == "in_lesson" else "optional",
                "why": str(v.get("why") or ""),
            })
    if not videos:  # always surface at least the best video as a supplementary resource
        for s in sources:
            if s.get("type") == "video":
                videos.append({"source_id": s["id"], "placement": "optional", "why": "Supplementary explainer video."})
                break

    if removed:
        notes.append(f"Removed citations to non-existent sources: {', '.join(sorted(removed))}")

    lesson = {
        "title": str(raw.get("title") or plan.get("title") or "").strip(),
        "objective": str(raw.get("objective") or plan.get("objective") or "").strip(),
        "audience": str(raw.get("audience") or plan.get("audience") or "").strip(),
        "prerequisites": _as_list(raw.get("prerequisites") or plan.get("prerequisites") or []),
        "sub_objectives": [lo for lo in (raw.get("sub_objectives") or plan.get("sub_objectives") or [])
                           if isinstance(lo, dict) and lo.get("id") and lo.get("text")],
        "sections": sections,
        "quiz": quiz,
        "videos": videos,
        "instructor_notes": str(raw.get("instructor_notes") or "").strip(),
        "extra_sources": [x for x in (raw.get("extra_sources") or []) if isinstance(x, dict) and x.get("id")],
    }
    return lesson, notes


def compute_timing(lesson: dict, sources: list[dict]) -> dict:
    """Deterministic time model (see docs/DESIGN.md):
    self-paced    = words / reading_wpm + code_lines * 0.10 + activity minutes
    instructor-led = (words / speaking_wpm + code_lines * 0.15) * 1.15 (questions/discussion) + activity minutes
    quiz: 1.0 min/question self-paced, 1.5 min/question instructor-led (incl. reviewing answers)
    videos: only those placed "in_lesson" count, at their real duration.
    """
    s = get_settings()
    by_id = {x["id"]: x for x in sources}
    rows, tot_self, tot_inst, words_total = [], 0.0, 0.0, 0
    for sec in lesson.get("sections", []):
        w, c = word_count(sec["content_md"]), code_lines(sec["content_md"])
        act = float(sec.get("activity_minutes") or 0)
        sp = w / s.reading_wpm + c * 0.10 + act
        il = (w / s.speaking_wpm + c * 0.15) * 1.15 + act
        rows.append({"section_id": sec["id"], "heading": sec["heading"], "type": sec["type"], "words": w,
                     "activity_minutes": act, "self_paced": round(sp, 1), "instructor_led": round(il, 1)})
        tot_self += sp
        tot_inst += il
        words_total += w
    nq = len(lesson.get("quiz", []))
    rows.append({"section_id": "quiz", "heading": f"Knowledge check ({nq} questions)", "type": "quiz", "words": 0,
                 "activity_minutes": 0, "self_paced": round(nq * 1.0, 1), "instructor_led": round(nq * 1.5, 1)})
    tot_self += nq * 1.0
    tot_inst += nq * 1.5
    for v in lesson.get("videos", []):
        if v.get("placement") == "in_lesson":
            mins = (by_id.get(v["source_id"], {}).get("duration_sec") or 0) / 60
            rows.append({"section_id": v["source_id"], "heading": f"Video: {by_id.get(v['source_id'], {}).get('title', '')}",
                         "type": "video", "words": 0, "activity_minutes": 0,
                         "self_paced": round(mins, 1), "instructor_led": round(mins, 1)})
            tot_self += mins
            tot_inst += mins
    budget = s.lesson_minutes
    worst = max(tot_self, tot_inst)
    return {
        "rows": rows,
        "words": words_total,
        "self_paced": round(tot_self, 1),
        "instructor_led": round(tot_inst, 1),
        "budget": budget,
        # The brief requires "completed within 45 minutes"; only a really thin lesson (< half the budget) is flagged.
        "within_budget": worst <= budget + 1 and tot_self >= budget * 0.5,
        "status": "over" if worst > budget + 1 else ("under" if tot_self < budget * 0.5 else "ok"),
    }


def lesson_as_text(lesson: dict, include_quiz: bool = True) -> str:
    """Compact text rendering for prompts (keeps section ids so reviewers can reference them)."""
    out = [f"# {lesson.get('title', '')}", f"Objective: {lesson.get('objective', '')}", ""]
    for s in lesson.get("sections", []):
        extra = f", activity {s['activity_minutes']} min" if s.get("activity_minutes") else ""
        out.append(f"## [{s['id']}] ({s['type']}{extra}; objectives: {', '.join(s.get('objectives', []))}) {s['heading']}")
        out.append(s["content_md"])
        out.append("")
    if include_quiz and lesson.get("quiz"):
        out.append("## [quiz] Knowledge check")
        for q in lesson["quiz"]:
            opts = " | ".join(f"{chr(65 + i)}) {o}" for i, o in enumerate(q["options"]))
            out.append(f"{q['id']} ({q.get('objective', '')}): {q['question']} — {opts} — ANSWER: "
                       f"{chr(65 + q['answer_index'])} — {q.get('explanation', '')}")
    return "\n".join(out)
