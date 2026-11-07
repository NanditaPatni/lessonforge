"""End-to-end and unit tests. Run offline with the mock LLM + fixture research:  pytest -q"""
from __future__ import annotations

import io
import os
import time
import zipfile

import pytest

os.environ["LLM_PROVIDER"] = "mock"
os.environ["RESEARCH_MODE"] = "mock"
os.environ.pop("QC_LLM_PROVIDER", None)

from fastapi.testclient import TestClient  # noqa: E402

HTTP = {"title": "HTTP Requests and Responses",
        "objective": "Explain GET and POST methods and distinguish between 2xx, 4xx, and 5xx response status categories."}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def wait_done(client, lid, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/lessons/{lid}").json()
        if not d["running"] and all(s["status"] != "running" for s in d["steps"].values()):
            return d
        time.sleep(0.1)
    raise TimeoutError


def test_rejects_empty_inputs(client):
    r = client.post("/api/lessons", json={"title": "   ", "objective": "Explain things clearly please"})
    assert r.status_code == 422
    r = client.post("/api/lessons", json={"title": "HTTP", "objective": ""})
    assert r.status_code == 422


def test_full_pipeline_and_export(client):
    lid = client.post("/api/lessons", json=HTTP).json()["id"]
    d = wait_done(client, lid)
    assert all(s["status"] == "done" for s in d["steps"].values()), d["steps"]
    lesson = d["version"]["content"]
    # every cited source exists (no hallucinated citations)
    ids = {s["id"] for s in d["research"]["sources"]}
    for sec in lesson["sections"]:
        assert set(sec["citations"]) <= ids
    # within 45 minutes for both delivery modes
    assert d["timing"]["self_paced"] <= 46 and d["timing"]["instructor_led"] <= 46
    # QC layers produce separate reports
    assert d["qc_research"]["layer"] == "research" and d["qc_research"]["claims"]
    assert d["qc_instruction"]["layer"] == "instruction"
    trace = d["qc_instruction"]["automated"]["traceability"]
    assert trace and all(r["complete"] for r in trace)
    assert d["qc_instruction"]["automated"]["missing_terms"] == []
    # export bundle contains lesson, references and both reports
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/lessons/{lid}/export?format=zip").content))
    names = {n.split("/", 1)[1] for n in z.namelist()}
    assert {"lesson.md", "lesson.html", "references.md", "qc1_research_report.md", "qc2_instruction_report.md"} <= names


def test_edit_versioning_and_conflict(client):
    lid = client.post("/api/lessons", json=HTTP).json()["id"]
    d = wait_done(client, lid)
    content, v = d["version"]["content"], d["version"]["version"]
    content["sections"][0]["content_md"] += "\n\nInstructor note added."
    r = client.put(f"/api/lessons/{lid}/content", json={"content": content, "base_version": v})
    assert r.status_code == 200 and r.json()["version"] == v + 1
    # stale base version is rejected instead of silently overwriting
    r = client.put(f"/api/lessons/{lid}/content", json={"content": content, "base_version": v})
    assert r.status_code == 409
    # earlier version still intact
    assert "Instructor note" not in client.get(f"/api/lessons/{lid}/versions/{v}").json()["content"]["sections"][0]["content_md"]


def test_apply_qc_recommendation_creates_version(client):
    lid = client.post("/api/lessons", json=HTTP).json()["id"]
    d = wait_done(client, lid)
    issue = d["qc_instruction"]["issues"][0]
    r = client.post(f"/api/lessons/{lid}/qc/apply", json={"report": "qc_instruction", "issue_ids": [issue["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == d["version"]["version"] + 1
    d2 = client.get(f"/api/lessons/{lid}").json()
    assert next(i for i in d2["qc_instruction"]["issues"] if i["id"] == issue["id"])["status"] == "applied"


def test_failed_step_can_be_retried(client, monkeypatch):
    from app.pipeline import orchestrator
    real = orchestrator.HANDLERS["generate"]
    calls = {"n": 0}

    def flaky(lid):
        calls["n"] += 1
        if calls["n"] <= 2:          # fails the first run AND its automatic retry
            raise RuntimeError("simulated model outage")
        return real(lid)

    monkeypatch.setitem(orchestrator.HANDLERS, "generate", flaky)
    lid = client.post("/api/lessons", json=HTTP).json()["id"]
    d = wait_done(client, lid)
    assert d["steps"]["generate"]["status"] == "failed" and "outage" in d["steps"]["generate"]["error"]
    assert d["steps"]["research"]["status"] == "done"  # earlier work preserved
    assert client.post(f"/api/lessons/{lid}/retry", json={}).json()["from"] == "generate"
    d = wait_done(client, lid)
    assert all(s["status"] == "done" for s in d["steps"].values())


def test_hallucinated_citations_are_stripped():
    from app.pipeline.lesson_model import normalize_lesson
    raw = {"sections": [{"id": "a", "type": "concept", "heading": "x", "content_md": "Fact [S1][S99]. Other [S2, S42]."}], "quiz": []}
    lesson, notes = normalize_lesson(raw, [{"id": "S1", "type": "web"}, {"id": "S2", "type": "web"}])
    assert "[S99]" not in lesson["sections"][0]["content_md"] and "[S42]" not in lesson["sections"][0]["content_md"]
    assert lesson["sections"][0]["citations"] == ["S1", "S2"]
    assert any("S99" in n for n in notes)


def test_bm25_ranks_relevant_passage_first():
    from app.research.text import BM25, tokenize
    docs = ["POST submits data in the body", "5xx means server error", "GET retrieves a representation"]
    bm = BM25([tokenize(d) for d in docs])
    assert bm.top(tokenize("server error 5xx"), 1)[0][1] == 1


def test_credibility_tiers():
    from app.research.credibility import score_web
    assert score_web("https://developer.mozilla.org/en-US/docs/Web/HTTP")["tier"] == "authoritative"
    assert score_web("https://medium.com/@x/http")["tier"] == "low"
    assert score_web("https://cs.stanford.edu/notes")["tier"] == "authoritative"


def test_chunked_generation_path(client, monkeypatch):
    """Low-TPM providers (Groq) generate outline + sections in batches; result must be identical in shape."""
    monkeypatch.setenv("COMPACT_PROMPTS", "1")
    lid = client.post("/api/lessons", json=HTTP).json()["id"]
    d = wait_done(client, lid)
    assert all(s["status"] == "done" for s in d["steps"].values()), d["steps"]
    lesson = d["version"]["content"]
    assert len(lesson["sections"]) >= 5 and all(s["content_md"] for s in lesson["sections"])
    assert any("batches" in e["message"] for e in d["events"])


def test_issue_tagged_quiz_but_about_a_section_edits_that_section():
    from app.pipeline.apply import apply_issues
    from app.llm import LLMClient
    lesson = {"title": "t", "objective": "o", "sub_objectives": [], "videos": [], "extra_sources": [],
              "sections": [{"id": "sec-6", "type": "activity", "heading": "Scenarios", "objectives": [], "activity_minutes": 5,
                            "content_md": "Scenario C: 403 means check for a server crash."}],
              "quiz": [{"id": "Q1", "question": "q?", "options": ["a", "b"], "answer_index": 0, "explanation": "", "objective": ""}]}
    issue = {"id": "R1", "section_id": "quiz", "description": "In activity sec-6, Scenario C says 403 is a server crash.",
             "suggestion": "Fix Scenario C", "fix": {"op": "rewrite_section", "instruction": "Fix Scenario C"}}
    new, res = apply_issues(lesson, [issue], {"sources": [], "passages": []}, LLMClient())
    assert res[0]["ok"] and "Revised section" in res[0]["message"]
    assert new["quiz"][0]["question"] == "q?"            # quiz untouched
    assert "Revision" in new["sections"][0]["content_md"]  # the activity section was the one edited


def test_passage_level_citations_are_normalised_to_sources():
    from app.pipeline.lesson_model import citations_in, normalize_lesson
    md = "Use POST for forms [S6.p8]. Check 403 [S5.p4, S3.p26]. Unknown [S99.p1]."
    raw = {"sections": [{"id": "s", "type": "activity", "heading": "h", "content_md": md}], "quiz": []}
    srcs = [{"id": f"S{i}", "type": "web"} for i in (3, 5, 6)]
    lesson, notes = normalize_lesson(raw, srcs)
    out = lesson["sections"][0]["content_md"]
    assert "[S6]" in out and "[S5][S3]" in out and "S99" not in out and ".p" not in out
    assert lesson["sections"][0]["citations"] == ["S3", "S5", "S6"]
    assert citations_in("x [S6.p8]") == ["S6"]


def test_qc_tolerates_odd_model_shapes(monkeypatch):
    """Models sometimes return a bare list, a list-wrapped fix, or bare numeric scores. QC must not crash."""
    from app import mock_llm
    from app.pipeline import qc_instruction, qc_research
    from app.llm import LLMClient
    from app.research.mock_data import mock_research
    research = mock_research("HTTP Requests and Responses", "Explain GET and POST")
    from app.pipeline.lesson_model import normalize_lesson
    raw = mock_llm._generate({"title": "HTTP Requests and Responses", "objective": "o", "plan": mock_llm._plan({"title": "HTTP"}), "research": research})
    raw["sub_objectives"] = mock_llm._plan({"title": "HTTP"})["sub_objectives"]
    lesson, _ = normalize_lesson(raw, research["sources"])
    monkeypatch.setattr(mock_llm, "_consistency", lambda ctx: [  # bare list + fix wrapped in a list
        {"category": "quiz_answer", "severity": "critical", "section_id": "quiz", "description": "x", "suggestion": "y",
         "fix": [{"op": "rewrite_quiz", "instruction": "fix"}]}])
    monkeypatch.setattr(mock_llm, "_instruction_review", lambda ctx: {"scores": {"clarity": 4, "pacing": "5"},
                                                                     "issues": {"a": {"description": "d", "severity": "minor"}}})
    r1 = qc_research.run(lesson, research, LLMClient("qc"), 1)
    assert any(i["fix"]["op"] == "rewrite_quiz" for i in r1["issues"])
    r2 = qc_instruction.run(lesson, research["sources"], LLMClient("qc"), 1)
    assert r2["scores"]["clarity"]["score"] == 4 and r2["scores"]["pacing"]["score"] == 5


def test_single_transient_failure_is_retried_automatically(client, monkeypatch):
    from app.pipeline import orchestrator
    real = orchestrator.HANDLERS["qc_research"]
    calls = {"n": 0}

    def once(lid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP 503 model busy")
        return real(lid)

    monkeypatch.setitem(orchestrator.HANDLERS, "qc_research", once)
    lid = client.post("/api/lessons", json=HTTP).json()["id"]
    d = wait_done(client, lid)
    assert all(s["status"] == "done" for s in d["steps"].values())
    assert any("retrying automatically" in e["message"] for e in d["events"])


def test_qc_parts_fail_softly(monkeypatch):
    """If the model is unavailable for one QC part, the report is still produced, with a warning."""
    from app import mock_llm
    from app.llm import LLMClient
    from app.pipeline import qc_instruction, qc_research
    from app.pipeline.lesson_model import normalize_lesson
    from app.research.mock_data import mock_research
    research = mock_research("HTTP Requests and Responses", "Explain GET and POST")
    plan = mock_llm._plan({"title": "HTTP"})
    raw = mock_llm._generate({"title": "HTTP Requests and Responses", "objective": "o", "plan": plan, "research": research})
    raw["sub_objectives"] = plan["sub_objectives"]
    lesson, _ = normalize_lesson(raw, research["sources"])

    def boom(ctx):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(mock_llm, "_consistency", boom)
    monkeypatch.setattr(mock_llm, "_instruction_review", boom)
    r1 = qc_research.run(lesson, research, LLMClient("qc"), 1)
    assert r1["claims"] and any("Consistency audit skipped" in w for w in r1["warnings"])
    r2 = qc_instruction.run(lesson, research["sources"], LLMClient("qc"), 1)
    assert r2["metrics"]["instructional_score"] is None and r2["automated"]["traceability"]
    assert any("Rubric review skipped" in w for w in r2["warnings"])


def test_with_deadline_stops_waiting_on_a_hung_call():
    import time
    from app.research.web import with_deadline
    t0 = time.time()
    with pytest.raises(TimeoutError):
        with_deadline(lambda: time.sleep(5), 0.5)
    assert time.time() - t0 < 2
    assert with_deadline(lambda x: x * 2, 2, 21) == 42


def test_low_credibility_answer_sites():
    from app.research.credibility import score_web
    assert score_web("https://askfilo.com/user-question-answers/q1")["tier"] == "low"
    assert score_web("https://www.coursehero.com/file/x")["tier"] == "low"


def test_quiz_tagged_issue_applies_to_quiz():
    from app.llm import LLMClient
    from app.pipeline.apply import apply_issues
    lesson = {"title": "t", "objective": "o", "sub_objectives": [], "videos": [], "extra_sources": [],
              "sections": [{"id": "sec-1", "type": "concept", "heading": "h", "objectives": [], "activity_minutes": 0, "content_md": "x"}],
              "quiz": [{"id": "Q1", "question": "q?", "options": ["a", "b"], "answer_index": 0, "explanation": "", "objective": ""}]}
    issue = {"id": "R2", "section_id": "quiz", "description": "Claim C19 not supported: The correct answer to Q2 is B",
             "suggestion": "Support it", "fix": {"op": "rewrite_section", "instruction": "Support it"}}
    new, res = apply_issues(lesson, [issue], {"sources": [], "passages": []}, LLMClient())
    assert res[0]["ok"] and "Knowledge check" in res[0]["message"]


def test_citations_never_appear_in_quiz_options():
    from app.pipeline.lesson_model import normalize_lesson
    raw = {"sections": [{"id": "s", "type": "concept", "heading": "h", "content_md": "x [S2]"}],
           "quiz": [{"question": "What does GET do? [S1]", "options": ["To fetch a resource [S2.p19]", "Delete [S3.p2, S3.p7]"],
                     "answer_index": 0, "explanation": "Because [S1]."}]}
    lesson, _ = normalize_lesson(raw, [{"id": f"S{i}", "type": "web"} for i in (1, 2, 3)])
    q = lesson["quiz"][0]
    assert "[" not in q["question"] and all("[" not in o for o in q["options"])
    assert q["options"][0] == "To fetch a resource"
    assert "[S1]" in q["explanation"] and "[S2]" in q["explanation"] and "[S3]" in q["explanation"]
