"""Apply QC recommendations chosen by the instructor.

Each fix is applied as precisely as possible:
  replace_text  -> deterministic string replacement (no LLM, no collateral changes)
  add_citation  -> registers the independently found source and inserts its [S#] marker
  rewrite_section / add_section / rewrite_quiz -> targeted LLM edit of ONE section or the quiz only
The result is saved as a new version; the previous version is always kept.
"""
from __future__ import annotations

import copy
import re
import json

from .. import prompts
from ..llm import LLMClient
from .evidence import EvidenceIndex, fmt_passages, fmt_sources
from ..research.text import tokenize
from .lesson_model import all_sources, normalize_lesson


def _section_index(lesson: dict, sid: str) -> int:
    for i, s in enumerate(lesson["sections"]):
        if s["id"] == sid:
            return i
    return -1


def _locate(lesson: dict, issue: dict) -> int:
    """Find the section an issue refers to when the reviewer gave no/invalid section id."""
    fix = issue.get("fix") or {}
    for key in ("find",):
        if fix.get(key):
            for k, s in enumerate(lesson["sections"]):
                if fix[key] in s["content_md"]:
                    return k
    q = set(tokenize(issue.get("description", "") + " " + (fix.get("instruction") or "")))
    best, best_k = 0, -1
    for k, s in enumerate(lesson["sections"]):
        ov = len(q & set(tokenize(s["heading"] + " " + s["content_md"])))
        if ov > best:
            best, best_k = ov, k
    return best_k


def _validate_section(d):
    if not isinstance(d, dict) or not str(d.get("content_md") or "").strip():
        raise ValueError("section must have non-empty content_md")


def _validate_quiz(d):
    if not isinstance(d.get("quiz"), list) or not d["quiz"]:
        raise ValueError("'quiz' list missing")


def apply_issues(lesson: dict, issues: list[dict], research: dict, llm: LLMClient) -> tuple[dict, list[dict]]:
    lesson = copy.deepcopy(lesson)
    results = []
    for issue in issues:
        fix = issue.get("fix") or {}
        op = fix.get("op")
        instruction = fix.get("instruction") or issue.get("suggestion") or issue.get("description")
        sid = issue.get("section_id") or ""
        try:
            sources = all_sources(research, lesson)
            extra_passages = [p for s in lesson.get("extra_sources", []) for p in s.get("passages", [])]
            idx = EvidenceIndex({"passages": research.get("passages", []), "sources": sources}, extra_passages)
            src_list = fmt_sources([s for s in sources if s["type"] != "video"])
            i = _section_index(lesson, sid)
            if op == "manual":
                results.append({"id": issue["id"], "ok": False, "message": "Needs a manual change (edit the lesson or references directly)"})
                continue
            if i < 0 and sid != "quiz" and op != "add_section":
                i = _locate(lesson, issue)

            if op == "replace_text" and fix.get("find") and i >= 0 and fix["find"] in lesson["sections"][i]["content_md"]:
                sec = lesson["sections"][i]
                sec["content_md"] = sec["content_md"].replace(fix["find"], fix.get("replace", ""), 1)
                results.append({"id": issue["id"], "ok": True, "message": "Replaced text exactly as recommended"})
                continue

            if op == "add_citation" and fix.get("source"):
                src = fix["source"]
                if src["id"] not in {s["id"] for s in sources}:
                    lesson.setdefault("extra_sources", []).append(src)
                if i >= 0 and fix.get("find") and fix["find"] in lesson["sections"][i]["content_md"]:
                    sec = lesson["sections"][i]
                    sec["content_md"] = sec["content_md"].replace(fix["find"], fix["find"].rstrip() + f" [{src['id']}]", 1)
                    results.append({"id": issue["id"], "ok": True, "message": f"Added independent source {src['id']} and cited it"})
                else:
                    results.append({"id": issue["id"], "ok": True, "message": f"Added source {src['id']} to references"})
                continue

            # A reviewer may tag an issue "quiz" while describing a mistake in a section (e.g. an activity).
            # Only treat it as a quiz edit when the fix really targets the quiz.
            if sid == "quiz" and op != "rewrite_quiz" and re.search(r"sec-\d+", issue.get("description", "") + instruction):
                sid = re.search(r"sec-\d+", issue.get("description", "") + instruction).group()
                i = _section_index(lesson, sid)
                if i < 0:
                    i = _locate(lesson, issue)
            if op == "rewrite_quiz" or sid == "quiz":
                ev = idx.search(instruction, 5)
                data = llm.json(prompts.REWRITE_SYSTEM, prompts.REWRITE_QUIZ_USER.format(
                    instruction=instruction, sources=src_list, evidence=fmt_passages(ev),
                    quiz=json.dumps(lesson["quiz"], indent=1),
                    sub_objectives="; ".join(f"{lo['id']}: {lo['text']}" for lo in lesson.get("sub_objectives", []))),
                    task="rewrite_quiz", context={"quiz": lesson["quiz"], "instruction": instruction}, validate=_validate_quiz)
                lesson["quiz"] = data["quiz"]
                results.append({"id": issue["id"], "ok": True, "message": "Knowledge check revised"})
                continue

            if op == "add_section":
                ev = idx.search(instruction, 5)
                new = llm.json(prompts.REWRITE_SYSTEM, prompts.ADD_SECTION_USER.format(
                    title=lesson["title"], instruction=instruction, sources=src_list, evidence=fmt_passages(ev),
                    sub_objectives="; ".join(f"{lo['id']}: {lo['text']}" for lo in lesson.get("sub_objectives", []))),
                    task="add_section", context={"instruction": instruction}, validate=_validate_section)
                new["id"] = f"sec-{len(lesson['sections']) + 1}-{issue['id'].lower()}"
                pos = i + 1 if i >= 0 else next((k for k, s in enumerate(lesson["sections"]) if s["type"] == "summary"), len(lesson["sections"]))
                lesson["sections"].insert(pos, new)
                results.append({"id": issue["id"], "ok": True, "message": f"Added section '{new.get('heading', '')}'"})
                continue

            if i < 0:
                raise ValueError("could not determine which section to change")
            sec = lesson["sections"][i]
            ev = idx.search(instruction + " " + sec["heading"], 5)
            new = llm.json(prompts.REWRITE_SYSTEM, prompts.REWRITE_SECTION_USER.format(
                instruction=instruction, sources=src_list, evidence=fmt_passages(ev), section=json.dumps(sec, indent=1)),
                task="rewrite_section", context={"section": sec, "instruction": instruction}, validate=_validate_section)
            new["id"], new["type"] = sec["id"], new.get("type") or sec["type"]
            new.setdefault("objectives", sec.get("objectives", []))
            lesson["sections"][i] = new
            results.append({"id": issue["id"], "ok": True, "message": f"Revised section '{sec['heading']}'"})
        except Exception as e:  # one failing fix must not block the others
            results.append({"id": issue["id"], "ok": False, "message": f"Could not apply: {e}"})

    lesson, notes = normalize_lesson(lesson, all_sources(research, lesson))
    if notes:
        results.append({"id": "-", "ok": True, "message": "; ".join(notes)})
    return lesson, results
