"""FastAPI application: REST API + static single-page UI."""
from __future__ import annotations

import difflib
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__, db, export
from .config import ROOT, get_settings
from .llm import LLMClient
from .pipeline import orchestrator
from .pipeline.apply import apply_issues
from .pipeline.lesson_model import all_sources, normalize_lesson

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="LessonForge", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# --------------------------------------------------------------------- models
class CreateLesson(BaseModel):
    title: str = Field(..., max_length=150)
    objective: str = Field(..., max_length=600)

    @field_validator("title", "objective")
    @classmethod
    def not_blank(cls, v: str, info):
        v = re.sub(r"\s+", " ", v or "").strip()
        minimum = 3 if info.field_name == "title" else 10
        if len(v) < minimum:
            raise ValueError(f"{info.field_name} is required (at least {minimum} characters)")
        return v


class SaveContent(BaseModel):
    content: dict
    base_version: int
    note: str = ""


class IssueIds(BaseModel):
    report: str  # qc_research | qc_instruction
    issue_ids: list[str]


class RetryReq(BaseModel):
    step: str | None = None


# ------------------------------------------------------------------- helpers
def _lesson_or_404(lid: str) -> dict:
    l = db.get_lesson(lid)
    if not l:
        raise HTTPException(404, "Lesson not found")
    return l


def _busy(lid: str):
    if orchestrator.is_running(lid):
        raise HTTPException(409, "The pipeline is currently running for this lesson. Wait for it to finish.")


def _research(lid: str) -> dict:
    return db.get_step_output(lid, "research") or {"sources": [], "passages": []}


# --------------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    s = get_settings()
    return {
        "version": __version__,
        "llm": f"{s.llm_provider}:{s.llm_model}",
        "qc_llm": f"{s.qc_provider}:{s.qc_model}",
        "independent_qc_model": (s.qc_provider, s.qc_model) != (s.llm_provider, s.llm_model),
        "llm_key_set": s.llm_provider in ("mock", "ollama") or bool(s.key_for(s.llm_provider)),
        "fallback_llm": ", ".join(f"{p}:{m}" for p, m in s.fallbacks) or None,
        "research_mode": s.research_mode,
        "web_search": "tavily" if s.tavily_key else "duckduckgo",
        "youtube": "youtube-data-api" if s.youtube_key else "duckduckgo-videos",
        "lesson_minutes": s.lesson_minutes,
        "reading_wpm": s.reading_wpm, "speaking_wpm": s.speaking_wpm,
    }


@app.get("/api/lessons")
def lessons():
    return db.list_lessons()


@app.post("/api/lessons", status_code=201)
def create(req: CreateLesson):
    lid = db.create_lesson(req.title, req.objective)
    db.log_event(lid, f"Lesson created: '{req.title}'")
    orchestrator.start(lid)
    return {"id": lid}


@app.get("/api/lessons/{lid}")
def get(lid: str):
    meta = _lesson_or_404(lid)
    # Read the running flag BEFORE step statuses: if it is False here, every step already finished,
    # so the statuses read next are final (avoids a race where a finished run still shows 'running').
    running = orchestrator.is_running(lid)
    steps = db.get_steps(lid)
    ver = db.get_version(lid)
    research = steps["research"]["output"] or {}
    timing = orchestrator.timing_for(lid, ver["content"]) if ver else None
    step_view = {}
    for name, st in steps.items():
        step_view[name] = {k: st[k] for k in ("status", "error", "attempts", "started_at", "finished_at")}
        step_view[name]["label"] = orchestrator.STEP_LABELS[name]
    return {
        "lesson": meta,
        "running": running,
        "steps": step_view,
        "plan": steps["plan"]["output"],
        "research": {"sources": all_sources(research, ver["content"] if ver else None), "warnings": research.get("warnings", []),
                     "stats": research.get("stats", {}), "mock": research.get("mock", False)} if research else None,
        "version": ver,
        "timing": timing,
        "qc_research": steps["qc_research"]["output"],
        "qc_instruction": steps["qc_instruction"]["output"],
        "events": db.get_events(lid, 80),
    }


@app.delete("/api/lessons/{lid}")
def delete(lid: str):
    _lesson_or_404(lid)
    _busy(lid)
    db.delete_lesson(lid)
    return {"ok": True}


@app.post("/api/lessons/{lid}/retry")
def retry(lid: str, req: RetryReq):
    _lesson_or_404(lid)
    steps = db.get_steps(lid, with_output=False)
    step = req.step or next((n for n in db.STEPS if steps[n]["status"] in ("failed", "pending")), None)
    if step not in db.STEPS:
        raise HTTPException(400, "Nothing to retry — all steps are complete. Choose a step to re-run.")
    if not orchestrator.start(lid, from_step=step):
        raise HTTPException(409, "Pipeline already running")
    db.log_event(lid, f"Retry requested from step '{step}'")
    return {"ok": True, "from": step}


@app.post("/api/lessons/{lid}/qc/run")
def rerun_qc(lid: str):
    _lesson_or_404(lid)
    if not db.get_version(lid):
        raise HTTPException(400, "Generate the lesson first")
    if not orchestrator.start(lid, from_step="qc_research"):
        raise HTTPException(409, "Pipeline already running")
    db.log_event(lid, "QC re-run requested on latest version")
    return {"ok": True}


@app.put("/api/lessons/{lid}/content")
def save(lid: str, req: SaveContent):
    _lesson_or_404(lid)
    _busy(lid)
    latest = db.get_version(lid)
    if latest and latest["version"] != req.base_version:
        raise HTTPException(409, f"Version conflict: you edited v{req.base_version} but v{latest['version']} is now latest. "
                                 "Your draft is kept in the browser — reload and re-apply.")
    try:
        content, notes = normalize_lesson(req.content, all_sources(_research(lid), req.content))
    except ValueError as e:
        raise HTTPException(422, f"Invalid lesson: {e}")
    v = db.add_version(lid, content, "instructor", req.note or "Instructor edit")
    db.log_event(lid, f"Instructor saved v{v}" + (f" ({'; '.join(notes)})" if notes else ""))
    return {"version": v, "notes": notes, "timing": orchestrator.timing_for(lid, content)}


@app.get("/api/lessons/{lid}/versions")
def versions(lid: str):
    _lesson_or_404(lid)
    return db.list_versions(lid)


@app.get("/api/lessons/{lid}/versions/{n}")
def version(lid: str, n: int):
    v = db.get_version(lid, n)
    if not v:
        raise HTTPException(404, "Version not found")
    return v


@app.post("/api/lessons/{lid}/versions/{n}/restore")
def restore(lid: str, n: int):
    _busy(lid)
    v = db.get_version(lid, n)
    if not v:
        raise HTTPException(404, "Version not found")
    new = db.add_version(lid, v["content"], "instructor", f"Restored from v{n}")
    db.log_event(lid, f"Restored v{n} as v{new}")
    return {"version": new}


@app.get("/api/lessons/{lid}/diff")
def diff(lid: str, a: int, b: int):
    va, vb = db.get_version(lid, a), db.get_version(lid, b)
    if not va or not vb:
        raise HTTPException(404, "Version not found")
    r = _research(lid)
    ta = export.lesson_markdown(va["content"], r, answer_key=True).splitlines()
    tb = export.lesson_markdown(vb["content"], r, answer_key=True).splitlines()
    return {"diff": "\n".join(difflib.unified_diff(ta, tb, f"v{a}", f"v{b}", lineterm="", n=2))}


@app.post("/api/lessons/{lid}/qc/apply")
def apply(lid: str, req: IssueIds):
    _lesson_or_404(lid)
    if req.report not in ("qc_research", "qc_instruction"):
        raise HTTPException(400, "report must be qc_research or qc_instruction")
    if not orchestrator.acquire(lid):
        raise HTTPException(409, "Pipeline already running")
    try:
        report = db.get_step_output(lid, req.report)
        latest = db.get_version(lid)
        if not report or not latest:
            raise HTTPException(400, "No QC report / lesson to apply to")
        chosen = [i for i in report["issues"] if i["id"] in req.issue_ids and i["status"] == "open"]
        if not chosen:
            raise HTTPException(400, "Select at least one open issue")
        db.log_event(lid, f"Applying {len(chosen)} recommendation(s) from {req.report}: {', '.join(i['id'] for i in chosen)}")
        new, results = apply_issues(latest["content"], chosen, _research(lid), LLMClient("generator"))
        ok_ids = [r["id"] for r in results if r["ok"] and r["id"] != "-"]
        if not ok_ids:
            return JSONResponse({"version": latest["version"], "results": results}, status_code=422)
        v = db.add_version(lid, new, "qc-apply", f"Applied {req.report} recommendations {', '.join(ok_ids)}")
        for i in report["issues"]:
            if i["id"] in ok_ids:
                i["status"] = "applied"
                i["applied_in"] = v
        db.update_step_output(lid, req.report, report)
        for r in results:
            db.log_event(lid, f"  {r['id']}: {r['message']}", level="info" if r["ok"] else "error")
        return {"version": v, "results": results}
    finally:
        orchestrator.release(lid)


@app.post("/api/lessons/{lid}/qc/dismiss")
def dismiss(lid: str, req: IssueIds):
    report = db.get_step_output(lid, req.report)
    if not report:
        raise HTTPException(404, "Report not found")
    for i in report["issues"]:
        if i["id"] in req.issue_ids:
            i["status"] = "dismissed" if i["status"] != "dismissed" else "open"
    db.update_step_output(lid, req.report, report)
    return {"ok": True}


@app.get("/api/lessons/{lid}/preview", response_class=HTMLResponse)
def preview(lid: str, version: int | None = None):
    v = db.get_version(lid, version)
    if not v:
        raise HTTPException(404, "No lesson yet")
    r = _research(lid)
    md = export.lesson_markdown(v["content"], r, orchestrator.timing_for(lid, v["content"]))
    return HTMLResponse(export.render_html(md, v["content"]["title"]))


@app.get("/api/lessons/{lid}/export")
def export_lesson(lid: str, format: str = Query("zip", pattern="^(zip|md|html|json)$"), version: int | None = None):
    _lesson_or_404(lid)
    v = db.get_version(lid, version)
    if not v:
        raise HTTPException(404, "No lesson to export yet")
    r = _research(lid)
    lesson = v["content"]
    timing = orchestrator.timing_for(lid, lesson)
    slug = re.sub(r"[^a-z0-9]+", "-", lesson["title"].lower()).strip("-")[:50] or "lesson"
    if format == "md":
        body, mime = export.lesson_markdown(lesson, r, timing), "text/markdown"
    elif format == "html":
        body, mime = export.render_html(export.lesson_markdown(lesson, r, timing), lesson["title"]), "text/html"
    elif format == "json":
        import json
        body, mime = json.dumps({"version": v["version"], "lesson": lesson, "timing": timing}, indent=2), "application/json"
    else:
        body = export.build_zip(lesson, r, timing, db.get_step_output(lid, "qc_research"),
                                db.get_step_output(lid, "qc_instruction"), db.list_versions(lid), v["version"])
        mime = "application/zip"
    return Response(body, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{slug}-v{v["version"]}.{format}"'})
