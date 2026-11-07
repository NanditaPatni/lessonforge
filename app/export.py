"""Export: lesson (Markdown / HTML / JSON), references, both QC reports, and a ZIP bundle."""
from __future__ import annotations

import html
import io
import json
import re
import zipfile

import markdown as md_lib

from .pipeline.lesson_model import all_sources, citations_in


def _fmt_min(x: float) -> str:
    return f"{x:g} min"


def cited_sources(lesson: dict, research: dict) -> list[dict]:
    srcs = {s["id"]: s for s in all_sources(research, lesson)}
    ids = set()
    for s in lesson["sections"]:
        ids.update(citations_in(s["content_md"]))
    for q in lesson.get("quiz", []):
        ids.update(citations_in(q.get("explanation", "")))
    ids.update(v["source_id"] for v in lesson.get("videos", []))
    # books are always useful further reading
    ids.update(s["id"] for s in srcs.values() if s["type"] == "book")
    return [srcs[i] for i in sorted(ids, key=lambda x: int(x[1:])) if i in srcs]


def reference_line(s: dict) -> str:
    if s["type"] == "book":
        authors = ", ".join(s.get("authors") or []) or "Unknown author"
        meta = f"{authors}. *{s['title']}*. {s.get('publisher') or ''} {s.get('year') or ''}".strip()
        if s.get("isbn"):
            meta += f". ISBN {s['isbn']}"
        return f"**[{s['id']}]** {meta}. {s['url']}"
    if s["type"] == "video":
        mins = f", {round((s.get('duration_sec') or 0) / 60, 1)} min" if s.get("duration_sec") else ""
        return f"**[{s['id']}]** {s['title']} — YouTube, {s.get('channel', '')}{mins}. {s['url']}"
    return f"**[{s['id']}]** {s['title']} — {s.get('domain', '')}. {s['url']}"


def lesson_markdown(lesson: dict, research: dict, timing: dict | None = None, answer_key: bool = True) -> str:
    srcs = {s["id"]: s for s in all_sources(research, lesson)}
    out = [f"# {lesson['title']}", "", f"**Learning objective:** {lesson['objective']}", ""]
    if lesson.get("audience"):
        out += [f"**Audience:** {lesson['audience']}", ""]
    if lesson.get("prerequisites"):
        out += ["**Prerequisites:** " + "; ".join(lesson["prerequisites"]), ""]
    if lesson.get("sub_objectives"):
        out += ["**By the end of this lesson you will be able to:**", ""]
        out += [f"- **{lo['id']}** {lo['text']}" for lo in lesson["sub_objectives"]] + [""]
    if timing:
        out += [f"## Lesson plan ({timing['budget']} minutes)", "",
                "| Segment | Self-paced | Instructor-led |", "|---|---|---|"]
        out += [f"| {r['heading']} | {_fmt_min(r['self_paced'])} | {_fmt_min(r['instructor_led'])} |" for r in timing["rows"]]
        out += [f"| **Total** | **{_fmt_min(timing['self_paced'])}** | **{_fmt_min(timing['instructor_led'])}** |", ""]
    for s in lesson["sections"]:
        label = f" *(activity · {s['activity_minutes']:g} min)*" if s.get("activity_minutes") else ""
        out += [f"## {s['heading']}{label}", "", s["content_md"], ""]
        if s["type"] == "summary":
            in_lesson = [v for v in lesson.get("videos", []) if v["placement"] == "in_lesson"]
            for v in in_lesson:
                src = srcs.get(v["source_id"], {})
                out += [f"> 🎬 **Watch in class:** [{src.get('title', '')}]({src.get('url', '')}) — {v['why']}", ""]
    if lesson.get("quiz"):
        out += ["## Knowledge check", ""]
        for i, q in enumerate(lesson["quiz"], 1):
            out += [f"**{i}. {q['question']}**", ""]
            out += [f"- {chr(65 + j)}) {o}" for j, o in enumerate(q["options"])] + [""]
    if lesson.get("videos"):
        out += ["## Supplementary videos", ""]
        for v in lesson["videos"]:
            src = srcs.get(v["source_id"])
            if src:
                dur = f" ({round((src.get('duration_sec') or 0) / 60, 1)} min)" if src.get("duration_sec") else ""
                tag = "in-lesson" if v["placement"] == "in_lesson" else "optional"
                out += [f"- [{src['title']}]({src['url']}){dur} — *{tag}* — {v['why']}"]
        out.append("")
    if answer_key and lesson.get("quiz"):
        out += ["## Answer key", ""]
        for i, q in enumerate(lesson["quiz"], 1):
            out += [f"{i}. **{chr(65 + q['answer_index'])}** — {q.get('explanation', '')}"]
        out.append("")
    if lesson.get("instructor_notes"):
        out += ["## Instructor notes", "", lesson["instructor_notes"], ""]
    refs = cited_sources(lesson, research)
    if refs:
        out += ["## References", ""] + [f"- {reference_line(s)}" for s in refs] + [""]
    return "\n".join(out)


CSS = """body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:860px;margin:2rem auto;padding:0 1.2rem;
line-height:1.65;color:#1f2328}h1{border-bottom:3px solid #d1453b;padding-bottom:.4rem}h2{margin-top:2.2rem;border-bottom:1px solid #e5e7eb;padding-bottom:.2rem}
code{background:#f3f4f6;padding:.1rem .3rem;border-radius:4px}pre{background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:8px;overflow:auto}
pre code{background:none;color:inherit}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #d1d5db;padding:.45rem .6rem;text-align:left}
th{background:#f9fafb}blockquote{border-left:4px solid #d1453b;margin:1rem 0;padding:.3rem 1rem;background:#fef2f2}
a.cite{font-size:.75em;vertical-align:super;text-decoration:none;color:#d1453b}"""


def render_html(markdown_text: str, title: str) -> str:
    body = md_lib.markdown(markdown_text, extensions=["tables", "fenced_code", "sane_lists"])
    body = re.sub(r"\[(S\d+)\]", r'<a class="cite" href="#ref-\1">[\1]</a>', body)
    body = re.sub(r"<strong><a class=\"cite\" href=\"#ref-(S\d+)\">\[S\d+\]</a></strong>",
                  r'<strong id="ref-\1">[\1]</strong>', body)
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")


def references_markdown(lesson: dict, research: dict) -> str:
    out = [f"# References — {lesson['title']}", "", "| ID | Type | Credibility | Source |", "|---|---|---|---|"]
    for s in cited_sources(lesson, research):
        out.append(f"| {s['id']} | {s['type']} | {s['credibility']['tier']} ({s['credibility']['score']}) — {s['credibility']['reason']} | [{s['title']}]({s['url']}) |")
    return "\n".join(out) + "\n"


def _cell(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).replace("|", "/").strip()


def qc_markdown(report: dict | None, title: str) -> str:
    if not report:
        return f"# QC report — {title}\n\nNot run yet.\n"
    name = "QC Layer 1 — Research & Factual Accuracy" if report["layer"] == "research" else "QC Layer 2 — Instructional Quality"
    out = [f"# {name}", "", f"**Lesson:** {title}  ", f"**Audited version:** v{report['version']}  ",
           f"**Reviewer model:** {report['model']}  ", f"**Verdict:** {report['status'].upper()}", "", "## Metrics", ""]
    out += [f"- {k.replace('_', ' ')}: **{'n/a' if v is None else v}**" for k, v in report["metrics"].items()] + [""]
    if report.get("warnings"):
        out += ["## Partial-report warnings", ""] + [f"- {w}" for w in report["warnings"]] + [""]
    if report["layer"] == "instruction":
        out += ["## Rubric", "", "| Criterion | Score | Justification |", "|---|---|---|"]
        out += [f"| {k.replace('_', ' ')} | {v['score']}/5 | {_cell(v['justification'])} |" for k, v in report["scores"].items()] + [""]
        out += ["## Objective traceability", "", "| Objective | Taught in | Practised in | Assessed by |", "|---|---|---|---|"]
        for r in report["automated"]["traceability"]:
            out.append(f"| {r['objective']}: {_cell(r['text'])} | {', '.join(r['taught']) or '❌'} | {', '.join(r['practised']) or '❌'} | {', '.join(r['assessed']) or '❌'} |")
        out.append("")
        if report.get("strengths"):
            out += ["## Strengths", ""] + [f"- {s}" for s in report["strengths"]] + [""]
    else:
        out += ["## Claim verification", "", "| ID | Claim | Evidence verdict | Expert check | Note |", "|---|---|---|---|---|"]
        for c in report["claims"]:
            note = c["explanation"]
            if c.get("second_opinion"):
                note += f" — independent check ({c['second_opinion']['url']}): {c['second_opinion']['verdict']}"
            out.append(f"| {c['id']} | {_cell(c['text'])} | {c['verdict']} | {c['knowledge_check']} | {_cell(note)} |")
        out += ["", "## Source reliability", "", "| ID | Tier | Cited | Link | Reason |", "|---|---|---|---|---|"]
        for s in report["sources"]:
            link = "n/a" if not s.get("link") else ("ok" if s["link"]["ok"] else f"broken ({s['link'].get('status')})")
            out.append(f"| {s['id']} | {s['tier']} ({s['score']}) | {s['times_cited']}× | {link} | {s['reason']} |")
        out.append("")
    out += ["## Issues and suggested improvements", ""]
    if not report["issues"]:
        out.append("No issues found.")
    for i in report["issues"]:
        out += [f"### {i['id']} · {i['severity'].upper()} · {i['category']} · status: {i['status']}",
                f"- **Location:** {i['section_id'] or 'whole lesson'}", f"- **Issue:** {i['description']}",
                f"- **Suggested improvement:** {i['suggestion']}", ""]
    return "\n".join(out) + "\n"


def build_zip(lesson: dict, research: dict, timing: dict, qc1: dict | None, qc2: dict | None,
              versions: list[dict], version: int) -> bytes:
    md = lesson_markdown(lesson, research, timing)
    buf = io.BytesIO()
    slug = re.sub(r"[^a-z0-9]+", "-", lesson["title"].lower()).strip("-")[:50] or "lesson"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{slug}/lesson.md", md)
        z.writestr(f"{slug}/lesson.html", render_html(md, lesson["title"]))
        z.writestr(f"{slug}/lesson.json", json.dumps({"version": version, "lesson": lesson, "timing": timing}, indent=2))
        z.writestr(f"{slug}/references.md", references_markdown(lesson, research))
        z.writestr(f"{slug}/qc1_research_report.md", qc_markdown(qc1, lesson["title"]))
        z.writestr(f"{slug}/qc2_instruction_report.md", qc_markdown(qc2, lesson["title"]))
        z.writestr(f"{slug}/qc_reports.json", json.dumps({"qc_research": qc1, "qc_instruction": qc2}, indent=2))
        z.writestr(f"{slug}/version_history.json", json.dumps(versions, indent=2))
    return buf.getvalue()
