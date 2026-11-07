"""Reply-shape normalisation: one place that turns the odd-but-parseable JSON models produce into the
shape each task expects, BEFORE validation. This removes a whole class of crashes (bare lists, extra
wrapper objects, renamed keys, options as dicts, numbers as strings) without extra model calls.
"""
from __future__ import annotations

import re
from typing import Any

# The key each task's reply must contain.
EXPECT = {
    "plan": "sub_objectives", "generate": "sections", "outline": "sections", "fit_budget": "sections",
    "sections": "sections", "extract_claims": "claims", "verify_claims": "results", "consistency": "issues",
    "instruction_review": "scores", "rewrite_section": "content_md", "add_section": "content_md", "rewrite_quiz": "quiz",
}
LIST_KEYS = {"sections", "claims", "results", "issues", "quiz"}
WRAPPERS = ("result", "results_obj", "lesson", "data", "response", "output", "section", "outline", "review", "plan", "answer")


def _unwrap(d: Any, key: str) -> Any:
    for _ in range(3):
        if isinstance(d, dict) and key not in d and len(d) == 1:
            inner = next(iter(d.values()))
            if isinstance(inner, (dict, list)):
                d = inner
                continue
        if isinstance(d, dict) and key not in d:
            for w in WRAPPERS:
                if isinstance(d.get(w), dict) and key in d[w]:
                    d = d[w]
                    break
        break
    return d


def _as_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        out = []
        for k, item in v.items():
            if isinstance(item, dict):
                item = dict(item)
                item.setdefault("id", str(k))
            out.append(item)
        return out
    if v in (None, ""):
        return []
    return [v]


def to_int(v: Any, default: int | None = None) -> int | None:
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+(\.\d+)?", str(v or ""))
    return int(float(m.group())) if m else default


def to_float(v: Any, default: float = 0.0) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.search(r"\d+(\.\d+)?", str(v or ""))
    return float(m.group()) if m else default


def fix_section(s: Any) -> Any:
    if not isinstance(s, dict):
        return s
    s = dict(s)
    if not s.get("content_md"):
        for alt in ("content", "markdown", "body", "text", "md"):
            if isinstance(s.get(alt), str) and s[alt].strip():
                s["content_md"] = s.pop(alt)
                break
    if "activity_minutes" in s:
        s["activity_minutes"] = to_float(s.get("activity_minutes"))
    if isinstance(s.get("objectives"), str):
        s["objectives"] = [x.strip() for x in re.split(r"[,;]", s["objectives"]) if x.strip()]
    if s.get("id") is not None:
        s["id"] = str(s["id"])
    return s


def fix_quiz_item(q: Any) -> Any:
    """Options as list; answer_index as an int index whatever form the model used (2, "2", "C", "C) ...", option text)."""
    if not isinstance(q, dict):
        return q
    q = dict(q)
    opts = q.get("options") or q.get("choices") or []
    if isinstance(opts, dict):
        opts = [opts[k] for k in sorted(opts, key=str)]
    if isinstance(opts, str):
        opts = [o.strip() for o in re.split(r"\n|\s*\|\s*", opts) if o.strip()]
    opts = [str(o).strip() for o in opts if str(o).strip()]
    q["options"] = opts
    raw = q.get("answer_index", q.get("answer", q.get("correct_answer", q.get("correct"))))
    idx = None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        idx = int(raw)
    elif isinstance(raw, str):
        r = raw.strip()
        if r.isdigit():
            idx = int(r)
        elif len(r) == 1 and r.upper() in "ABCDEFGH":
            idx = "ABCDEFGH".index(r.upper())
        elif re.match(r"^[A-Ha-h][\).:]\s", r):
            idx = "ABCDEFGH".index(r[0].upper())
        else:
            low = [o.lower() for o in opts]
            idx = low.index(r.lower()) if r.lower() in low else next((i for i, o in enumerate(low) if r.lower() in o or o in r.lower()), None)
    q["answer_index"] = idx if idx is not None else -1
    return q


SEVERITY = {"critical": "critical", "blocker": "critical", "high": "major", "major": "major", "medium": "minor",
            "moderate": "minor", "minor": "minor", "low": "minor", "info": "minor", "trivial": "minor"}


def severity(v: Any) -> str:
    return SEVERITY.get(str(v or "").strip().lower(), "minor")


def normalize_reply(task: str, d: Any) -> Any:
    key = EXPECT.get(task)
    if not key:
        return d
    d = _unwrap(d, key)
    if isinstance(d, list):
        if key in LIST_KEYS:
            d = {key: d}
        elif task == "instruction_review":
            d = {"issues": d, "scores": {}}
        elif task in ("rewrite_section", "add_section") and d and isinstance(d[0], dict):
            d = d[0]
    if not isinstance(d, dict):
        return d

    if task == "plan":
        los = d.get("sub_objectives")
        if isinstance(los, (list, dict)):
            d["sub_objectives"] = [lo if isinstance(lo, dict) else {"text": str(lo)} for lo in _as_list(los)]
        sq = d.get("search_queries")
        if isinstance(sq, list):
            d["search_queries"] = {"web": [str(x) for x in sq][:4], "books": [str(x) for x in sq][:1], "videos": [str(x) for x in sq][:1]}
        elif isinstance(sq, str):
            d["search_queries"] = {"web": [sq], "books": [sq], "videos": [sq]}
        elif isinstance(sq, dict):
            d["search_queries"] = {k: ([v] if isinstance(v, str) else [str(x) for x in _as_list(v)]) for k, v in sq.items()}
        for k in ("key_concepts", "common_misconceptions", "prerequisites"):
            if isinstance(d.get(k), str):
                d[k] = [d[k]]
    if "sections" in d and task in ("generate", "outline", "fit_budget", "sections"):
        d["sections"] = [fix_section(s) for s in _as_list(d["sections"])]
    if "quiz" in d:
        d["quiz"] = [fix_quiz_item(q) for q in _as_list(d["quiz"])]
    if isinstance(d.get("videos"), dict):
        d["videos"] = _as_list(d["videos"])
    if task in ("rewrite_section", "add_section"):
        d = fix_section(d)
    if task == "extract_claims":
        d["claims"] = [c if isinstance(c, dict) else {"text": str(c), "quote": str(c)} for c in _as_list(d.get("claims"))]
    if task == "verify_claims":
        d["results"] = [r for r in _as_list(d.get("results")) if isinstance(r, dict)]
    if task in ("consistency", "instruction_review"):
        d["issues"] = [i for i in _as_list(d.get("issues")) if isinstance(i, dict)]
    if task == "instruction_review":
        if not isinstance(d.get("scores"), dict):
            d["scores"] = {}
        if isinstance(d.get("strengths"), str):
            d["strengths"] = [x.strip() for x in re.split(r";|\n", d["strengths"]) if x.strip()]
    return d
