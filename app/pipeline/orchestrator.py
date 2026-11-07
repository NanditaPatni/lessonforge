"""Resumable pipeline runner.

plan -> research -> generate -> qc_research -> qc_instruction

* Each step's output is persisted; a failure stops the run with a readable error and the
  instructor can retry *from that step* without repeating (or paying for) earlier steps.
* Runs in a background thread; the UI polls status.
* Re-generating never destroys instructor edits — it adds a new version.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

from .. import db
from ..llm import LLMClient, LLMError
from ..research.collector import ResearchError, collect
from . import generator, planner, qc_instruction, qc_research
from .lesson_model import all_sources, compute_timing

log = logging.getLogger("lessonforge.pipeline")
STEPS = db.STEPS
STEP_LABELS = {"plan": "Plan objectives & queries", "research": "Research sources", "generate": "Generate lesson",
               "qc_research": "QC 1 · Research & facts", "qc_instruction": "QC 2 · Instructional quality"}

_running: set[str] = set()
_lock = threading.Lock()


class StepFailed(RuntimeError):
    pass


def is_running(lid: str) -> bool:
    with _lock:
        return lid in _running


def acquire(lid: str) -> bool:
    with _lock:
        if lid in _running:
            return False
        _running.add(lid)
        return True


def release(lid: str) -> None:
    with _lock:
        _running.discard(lid)


def start(lid: str, from_step: str | None = None, only: list[str] | None = None, background: bool = True) -> bool:
    if not acquire(lid):
        return False
    if from_step:
        for s in STEPS[STEPS.index(from_step):]:
            db.set_step(lid, s, "pending")
    if background:
        threading.Thread(target=_run, args=(lid, only), daemon=True).start()
    else:
        _run(lid, only)
    return True


def _run(lid: str, only: list[str] | None) -> None:
    try:
        steps = db.get_steps(lid, with_output=False)
        for name in STEPS:
            if only and name not in only:
                continue
            if steps[name]["status"] == "done" and not only:
                continue
            run_step(lid, name)
        db.log_event(lid, "Pipeline finished")
    except StepFailed:
        pass
    finally:
        release(lid)


def _friendly(e: Exception) -> str:
    if isinstance(e, (LLMError, ResearchError)):
        return str(e)
    if isinstance(e, KeyError):
        return f"Missing data from a previous step ({e}). Retry from an earlier step."
    return f"{type(e).__name__}: {e}"


AUTO_RETRY_WAIT = 20   # seconds before automatically retrying a failed step once (free tiers recover quickly)


def run_step(lid: str, name: str) -> None:
    db.set_step(lid, name, "running")
    db.log_event(lid, f"▶ {STEP_LABELS[name]} started")
    t0 = time.time()
    try:
        try:
            out = HANDLERS[name](lid)
        except Exception as first:  # noqa: BLE001
            # One automatic retry: most failures on free tiers (busy model, rate limit, odd reply) are transient.
            if isinstance(first, KeyError):
                raise
            db.log_event(lid, f"  ⚠ {STEP_LABELS[name]} hit an error ({_friendly(first)[:160]}); retrying automatically in {AUTO_RETRY_WAIT}s",
                         level="warn")
            time.sleep(AUTO_RETRY_WAIT)
            out = HANDLERS[name](lid)
        db.set_step(lid, name, "done", output=out)
        db.log_event(lid, f"✓ {STEP_LABELS[name]} done in {time.time() - t0:.1f}s")
    except Exception as e:
        log.error("step %s failed for %s: %s\n%s", name, lid, e, traceback.format_exc())
        msg = _friendly(e)
        db.set_step(lid, name, "failed", error=msg)
        db.log_event(lid, f"✗ {STEP_LABELS[name]} failed: {msg}", level="error")
        raise StepFailed(msg) from e


# ------------------------------------------------------------------ handlers
def _emit(lid: str):
    return lambda m: db.log_event(lid, "  " + m)


def _plan(lid: str) -> dict:
    lesson = db.get_lesson(lid)
    return planner.make_plan(lesson["title"], lesson["objective"], LLMClient("generator"))


def _research(lid: str) -> dict:
    lesson = db.get_lesson(lid)
    plan = db.get_step_output(lid, "plan")
    if not plan:
        raise KeyError("plan")
    res = collect(plan, lesson["title"], lesson["objective"], emit=_emit(lid))
    for w in res.get("warnings", []):
        db.log_event(lid, "  ⚠ " + w, level="warn")
    return res


def _generate(lid: str) -> dict:
    meta = db.get_lesson(lid)
    plan, research = db.get_step_output(lid, "plan"), db.get_step_output(lid, "research")
    if not plan or not research:
        raise KeyError("plan/research")
    llm = LLMClient("generator")
    lesson, timing, notes = generator.generate(meta["title"], meta["objective"], plan, research, llm, emit=_emit(lid))
    prev = db.get_version(lid)
    note = "AI generated" + (" (previous versions kept)" if prev else "")
    v = db.add_version(lid, lesson, "ai", note)
    for n in notes:
        db.log_event(lid, "  " + n)
    return {"version": v, "timing": timing, "notes": notes, "model": llm.label, "llm_calls": llm.calls}


def _latest(lid: str) -> tuple[dict, dict]:
    ver = db.get_version(lid)
    research = db.get_step_output(lid, "research")
    if not ver or not research:
        raise KeyError("lesson version / research")
    return ver, research


def _qc_research(lid: str) -> dict:
    ver, research = _latest(lid)
    return qc_research.run(ver["content"], research, LLMClient("qc"), ver["version"], emit=_emit(lid))


def _qc_instruction(lid: str) -> dict:
    ver, research = _latest(lid)
    return qc_instruction.run(ver["content"], all_sources(research, ver["content"]), LLMClient("qc"), ver["version"], emit=_emit(lid))


HANDLERS = {"plan": _plan, "research": _research, "generate": _generate,
            "qc_research": _qc_research, "qc_instruction": _qc_instruction}


def timing_for(lid: str, content: dict) -> dict:
    research = db.get_step_output(lid, "research") or {"sources": []}
    return compute_timing(content, all_sources(research, content))
