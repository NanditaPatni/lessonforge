"""Common issue format shared by both QC layers (and consumed by the apply step / UI)."""
from __future__ import annotations

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
FIX_OPS = {"replace_text", "rewrite_section", "rewrite_quiz", "add_section", "add_citation", "manual"}


def as_object(data, list_key: str) -> dict:
    """Models sometimes return a bare JSON list where an object was asked for: wrap it."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {list_key: data}
    return {}


def as_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):          # e.g. {"I1": {...}, "I2": {...}}
        return list(v.values())
    return []


def make_issue(category: str, severity: str, section_id: str, description: str, suggestion: str,
               fix: dict | None = None, source: str = "auto", **extra) -> dict:
    from ..shapes import severity as _sev
    severity = _sev(severity)
    if isinstance(fix, list):           # some models wrap the fix in a list
        fix = next((f for f in fix if isinstance(f, dict)), {})
    fix = dict(fix) if isinstance(fix, dict) else {}
    if fix.get("op") not in FIX_OPS:
        fix = {"op": "rewrite_quiz" if section_id == "quiz" else "rewrite_section", "instruction": suggestion}
    fix.setdefault("instruction", suggestion)
    return {"id": "", "category": category, "severity": severity, "section_id": section_id or "",
            "description": description, "suggestion": suggestion, "fix": fix,
            "detected_by": source, "status": "open", **extra}


def finalize(issues: list[dict], prefix: str) -> list[dict]:
    # de-duplicate near-identical descriptions, sort by severity, assign stable ids
    seen, out = set(), []
    for i in issues:
        key = (i["section_id"], i["description"][:80].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    out.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    for n, i in enumerate(out, 1):
        i["id"] = f"{prefix}{n}"
    return out


def llm_issue(raw: dict, valid_sections: set[str], source: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    desc = raw.get("description") or raw.get("issue") or raw.get("problem") or raw.get("message")
    if not desc:
        return None
    raw = {**raw, "description": desc}
    sid = str(raw.get("section_id") or "")
    if sid not in valid_sections and sid != "quiz":
        sid = ""
    return make_issue(str(raw.get("category") or "general"), str(raw.get("severity") or "minor").lower(), sid,
                      str(raw["description"]), str(raw.get("suggestion") or ""), raw.get("fix"), source=source)
