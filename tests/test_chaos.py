"""Chaos test: a fake model that randomly returns the odd JSON shapes real LLMs produce.

For every task the model's (correct) answer is randomly mutated: bare lists instead of objects, objects
wrapped in an extra key, list items turned into strings, numbers as strings, quiz options as a dict,
missing optional fields, rubric scores as plain numbers, etc. A mutation that the validator rejects is
followed by a clean answer (a real model corrects itself when re-asked). Every pipeline step must finish
and the lesson must still be usable: no crashes on malformed-but-parseable output.
"""
from __future__ import annotations

import copy
import random

import pytest


def mutate(task: str, data, rng: random.Random):
    d = copy.deepcopy(data)
    choice = rng.randrange(12)
    if choice == 0 and isinstance(d, dict):                       # wrap in an extra object
        return {rng.choice(["result", "lesson", "data", "response"]): d}
    if choice == 1 and isinstance(d, dict):                       # bare list instead of {"x": [...]}
        lists = [v for v in d.values() if isinstance(v, list)]
        if len(d) == 1 and lists:
            return lists[0]
    if choice == 2 and isinstance(d, dict):                       # drop a random optional key
        optional = [k for k in d if k not in ("sections", "quiz", "claims", "results", "sub_objectives", "search_queries")]
        if optional:
            d.pop(rng.choice(optional))
    if choice == 3 and isinstance(d, dict) and isinstance(d.get("quiz"), list):   # options as dict, answer as text/letter
        for q in d["quiz"]:
            if isinstance(q, dict) and isinstance(q.get("options"), list):
                opts = q["options"]
                q["options"] = {chr(65 + i): o for i, o in enumerate(opts)}
                ai = q.get("answer_index", 0)
                q["answer_index"] = rng.choice([chr(65 + int(ai)), opts[int(ai)] if int(ai) < len(opts) else "A", str(ai)])
    if choice == 4 and isinstance(d, dict) and isinstance(d.get("scores"), dict):  # scores as plain numbers / strings
        d["scores"] = {k: rng.choice([v.get("score", 3) if isinstance(v, dict) else v, "4/5", "3"]) for k, v in d["scores"].items()}
    if choice == 5 and isinstance(d, dict) and isinstance(d.get("issues"), list):  # issues: fix as list, odd severities
        for it in d["issues"]:
            if isinstance(it, dict):
                it["severity"] = rng.choice(["CRITICAL", "High", "low", "major", None])
                if isinstance(it.get("fix"), dict):
                    it["fix"] = [it["fix"]]
    if choice == 6 and isinstance(d, dict) and isinstance(d.get("sub_objectives"), list):  # objectives as strings
        d["sub_objectives"] = [lo["text"] if isinstance(lo, dict) else lo for lo in d["sub_objectives"]]
    if choice == 7 and isinstance(d, dict) and isinstance(d.get("search_queries"), dict):  # queries as a flat list
        d["search_queries"] = [q for v in d["search_queries"].values() for q in (v if isinstance(v, list) else [v])]
    if choice == 8 and isinstance(d, dict) and isinstance(d.get("sections"), list):  # content under another key, numeric ids
        for i, s in enumerate(d["sections"]):
            if isinstance(s, dict) and "content_md" in s and rng.random() < 0.5:
                s["content"] = s.pop("content_md")
            if isinstance(s, dict) and rng.random() < 0.3:
                s["activity_minutes"] = str(s.get("activity_minutes", 0))
    if choice == 9 and isinstance(d, dict) and isinstance(d.get("claims"), list):  # claims as plain strings
        d["claims"] = [c["text"] if isinstance(c, dict) and rng.random() < 0.5 else c for c in d["claims"]]
    if choice == 10 and isinstance(d, dict) and isinstance(d.get("results"), list):  # results keyed by id
        d["results"] = {r["id"]: r for r in d["results"] if isinstance(r, dict) and "id" in r}
    if choice == 11 and isinstance(d, dict) and isinstance(d.get("strengths"), list):
        d["strengths"] = "; ".join(map(str, d["strengths"]))
    return d


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("topic", ["http", "generic"])
def test_pipeline_survives_malformed_model_output(seed, topic, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RESEARCH_MODE", "mock")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "chaos.db"))
    monkeypatch.setenv("GENERATION_MODE", "chunked" if seed % 2 == 0 else "single")
    monkeypatch.setattr("time.sleep", lambda s: None)
    rng = random.Random(seed)

    from app import db, llm, mock_llm
    from app.pipeline import orchestrator
    monkeypatch.setattr(orchestrator, "AUTO_RETRY_WAIT", 0, raising=False)

    real = mock_llm.respond

    def chaotic_json(self, system, user, task, context=None, validate=None, temperature=0.3):
        clean = real(task, context or {})
        for attempt in range(3):
            data = clean
            if attempt == 0:                      # first answer: 1-3 stacked mutations; a re-asked model answers cleanly
                for _ in range(rng.randint(1, 3)):
                    data = mutate(task, data, rng)
            try:
                data = llm.normalize_reply(task, data)
                if validate:
                    validate(data)
                return data
            except (ValueError, KeyError, TypeError, IndexError):
                continue
        raise llm.LLMError(f"chaos: no valid reply for {task}")

    monkeypatch.setattr(llm.LLMClient, "json", chaotic_json)
    db.init()
    title, obj = (("HTTP Requests and Responses", "Explain GET and POST methods and distinguish between 2xx, 4xx, and 5xx response status categories.")
                  if topic == "http" else ("SQL Joins", "Differentiate INNER JOIN and LEFT JOIN and predict the rows each returns"))
    lid = db.create_lesson(title, obj)
    orchestrator.start(lid, background=False)
    steps = db.get_steps(lid)
    assert all(s["status"] == "done" for s in steps.values()), {k: v["error"] for k, v in steps.items()}

    lesson = db.get_version(lid)["content"]
    assert lesson["sections"] and all(s["content_md"] for s in lesson["sections"])
    for q in lesson["quiz"]:
        assert isinstance(q["options"], list) and 0 <= q["answer_index"] < len(q["options"])
    qc2 = steps["qc_instruction"]["output"]
    assert all(1 <= v["score"] <= 5 for v in qc2["scores"].values())

    # applying every open recommendation must never crash either
    from app.pipeline.apply import apply_issues
    issues = [i for i in steps["qc_research"]["output"]["issues"] + qc2["issues"] if i["status"] == "open"]
    new, results = apply_issues(lesson, issues, steps["research"]["output"], llm.LLMClient())
    assert new["sections"] and len(results) >= len(issues)
