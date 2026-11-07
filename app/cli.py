"""Headless runner: generate a lesson + both QC reports and write the export bundle.

    python -m app.cli --title "HTTP Requests and Responses" \
        --objective "Explain GET and POST methods and distinguish between 2xx, 4xx, and 5xx response status categories." \
        --apply-critical
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db, export
from .config import ROOT
from .llm import LLMClient
from .pipeline import orchestrator
from .pipeline.apply import apply_issues
from .pipeline.lesson_model import all_sources


def main() -> int:
    ap = argparse.ArgumentParser(description="LessonForge headless pipeline")
    ap.add_argument("--title", required=True)
    ap.add_argument("--objective", required=True)
    ap.add_argument("--out", default=str(ROOT / "samples"))
    ap.add_argument("--apply-critical", action="store_true",
                    help="auto-apply critical/major QC1 fixes, then re-run QC (demonstrates the improvement loop)")
    a = ap.parse_args()

    db.init()
    lid = db.create_lesson(a.title.strip(), a.objective.strip())
    print(f"Lesson {lid}: running pipeline…")
    orchestrator.start(lid, background=False)
    steps = db.get_steps(lid, with_output=False)
    for name, st in steps.items():
        print(f"  {name:15s} {st['status']}" + (f"  — {st['error']}" if st["error"] else ""))
    if any(st["status"] != "done" for st in steps.values()):
        print("Pipeline did not complete. Fix the error above and re-run.")
        return 1

    if a.apply_critical:
        qc1 = db.get_step_output(lid, "qc_research")
        chosen = [i for i in qc1["issues"] if i["severity"] in ("critical", "major")]
        if chosen:
            print(f"Applying {len(chosen)} critical/major QC1 fixes…")
            latest = db.get_version(lid)
            new, results = apply_issues(latest["content"], chosen, db.get_step_output(lid, "research"), LLMClient())
            for r in results:
                print(f"  {r['id']}: {'ok' if r['ok'] else 'FAILED'} — {r['message']}")
            db.add_version(lid, new, "qc-apply", "CLI: applied critical/major QC1 fixes")
            orchestrator.start(lid, from_step="qc_research", background=False)

    ver = db.get_version(lid)
    research = db.get_step_output(lid, "research")
    timing = orchestrator.timing_for(lid, ver["content"])
    data = export.build_zip(ver["content"], research, timing, db.get_step_output(lid, "qc_research"),
                            db.get_step_output(lid, "qc_instruction"), db.list_versions(lid), ver["version"])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    slug = export.re.sub(r"[^a-z0-9]+", "-", a.title.lower()).strip("-")[:50]
    path = out / f"{slug}.zip"
    path.write_bytes(data)
    q1, q2 = db.get_step_output(lid, "qc_research"), db.get_step_output(lid, "qc_instruction")
    print(f"\nTiming: self-paced {timing['self_paced']} min · instructor-led {timing['instructor_led']} min (budget {timing['budget']})")
    print(f"QC1: {q1['status']} · grounding {q1['metrics']['grounding_score']}% · {len(q1['issues'])} issues")
    print(f"QC2: {q2['status']} · score {q2['metrics']['instructional_score']}/100 · {len(q2['issues'])} issues")
    print(f"Sources: {len(all_sources(research, ver['content']))} · Export: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
