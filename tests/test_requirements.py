"""Acceptance tests: one test per requirement in the assignment brief (Kalvium — AI Research Intern).

Each test name quotes the brief. Runs offline (mock model + fixture research) so evaluators can verify
the workflow without API keys:  pytest tests/test_requirements.py -v
"""
from __future__ import annotations

import io
import re
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

HTTP = {"title": "HTTP Requests and Responses",
        "objective": "Explain GET and POST methods and distinguish between 2xx, 4xx, and 5xx response status categories."}
SQL = {"title": "SQL Joins: INNER JOIN vs LEFT JOIN",
       "objective": "Differentiate INNER JOIN and LEFT JOIN and predict the rows each returns for a given pair of tables."}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RESEARCH_MODE", "mock")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "req.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def run(client, payload) -> tuple[str, dict]:
    lid = client.post("/api/lessons", json=payload).json()["id"]
    for _ in range(300):
        d = client.get(f"/api/lessons/{lid}").json()
        if not d["running"] and all(s["status"] != "running" for s in d["steps"].values()):
            return lid, d
        time.sleep(0.05)
    raise TimeoutError


# ---------------------------------------------------------------- 1. AI-powered lesson generation
def test_1_accept_lesson_title_and_learning_objective_as_inputs(client):
    lid, d = run(client, HTTP)
    assert d["lesson"]["title"] == HTTP["title"] and d["lesson"]["objective"] == HTTP["objective"]
    assert all(s["status"] == "done" for s in d["steps"].values())


def test_1_research_books_websites_and_youtube(client):
    _, d = run(client, HTTP)
    types = {s["type"] for s in d["research"]["sources"]}
    assert {"web", "book", "video"} <= types
    assert all("tier" in s["credibility"] and s["credibility"]["reason"] for s in d["research"]["sources"])


def test_1_structured_content_with_explanations_examples_practical_applications(client):
    _, d = run(client, HTTP)
    types = [s["type"] for s in d["version"]["content"]["sections"]]
    assert types[0] == "hook" and types[-1] == "summary"
    assert {"concept", "example", "activity"} <= set(types)


def test_1_youtube_videos_included_as_supplementary_resources(client):
    lid, d = run(client, HTTP)
    assert d["version"]["content"]["videos"]
    md = client.get(f"/api/lessons/{lid}/export?format=md").text
    assert "## Supplementary videos" in md and "youtube.com" in md


def test_1_reference_links_at_end_of_lesson(client):
    lid, _ = run(client, HTTP)
    md = client.get(f"/api/lessons/{lid}/export?format=md").text
    refs = md.split("## References", 1)
    assert len(refs) == 2 and re.search(r"https?://", refs[1])
    assert md.rstrip().endswith(refs[1].rstrip())          # references are the last section


def test_1_completable_within_45_minutes_including_activities_and_knowledge_checks(client):
    _, d = run(client, HTTP)
    t = d["timing"]
    assert t["budget"] == 45 and t["self_paced"] <= 46 and t["instructor_led"] <= 46
    assert any(r["type"] == "quiz" for r in t["rows"]) and any(r["activity_minutes"] for r in t["rows"])
    assert len(d["version"]["content"]["quiz"]) >= 5


# ---------------------------------------------------------------- 2. Two QC layers
def test_2_qc1_verifies_facts_and_flags_unsupported_or_misleading_claims(client):
    _, d = run(client, HTTP)
    r = d["qc_research"]
    assert r["layer"] == "research" and r["claims"]
    assert {c["verdict"] for c in r["claims"]} <= {"supported", "partially_supported", "unsupported", "contradicted", "unchecked"}


def test_2_qc1_checks_reliability_and_relevance_of_sources(client):
    _, d = run(client, HTTP)
    assert all({"tier", "score", "reason", "times_cited"} <= set(s) for s in d["qc_research"]["sources"])


def test_2_qc1_identifies_gaps_or_inconsistencies(client):
    _, d = run(client, HTTP)
    cats = {i["category"] for i in d["qc_research"]["issues"]}
    assert cats & {"factual_gap", "inconsistency", "misleading", "citation", "unsupported_claim", "quiz_answer"}


def test_2_qc2_checks_objective_alignment_clarity_structure_and_gaps(client):
    _, d = run(client, HTTP)
    r = d["qc_instruction"]
    assert r["layer"] == "instruction"
    assert {"objective_alignment", "clarity", "structure", "examples", "practical_application"} <= set(r["scores"])
    assert r["automated"]["traceability"] and all(row["complete"] for row in r["automated"]["traceability"])


def test_2_both_layers_produce_separate_audit_reports_with_issues_and_suggestions(client):
    lid, d = run(client, HTTP)
    for key in ("qc_research", "qc_instruction"):
        assert all(i["description"] and i["suggestion"] for i in d[key]["issues"])
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/lessons/{lid}/export?format=zip").content))
    names = {n.split("/", 1)[1] for n in z.namelist()}
    assert {"qc1_research_report.md", "qc2_instruction_report.md"} <= names


# ---------------------------------------------------------------- 3. Review, edit, export
def test_3_instructors_can_review_edit_and_save(client):
    lid, d = run(client, HTTP)
    content = d["version"]["content"]
    content["sections"][1]["content_md"] += "\n\nInstructor addition."
    r = client.put(f"/api/lessons/{lid}/content", json={"content": content, "base_version": d["version"]["version"]})
    assert r.status_code == 200
    assert "Instructor addition." in client.get(f"/api/lessons/{lid}").json()["version"]["content"]["sections"][1]["content_md"]


def test_3_instructors_can_review_and_apply_qc_recommendations(client):
    lid, d = run(client, HTTP)
    issue = next(i for i in d["qc_instruction"]["issues"] if i["fix"]["op"] != "manual")
    r = client.post(f"/api/lessons/{lid}/qc/apply", json={"report": "qc_instruction", "issue_ids": [issue["id"]]})
    assert r.status_code == 200 and r.json()["version"] == d["version"]["version"] + 1
    r = client.post(f"/api/lessons/{lid}/qc/dismiss", json={"report": "qc_research", "issue_ids": [d["qc_research"]["issues"][0]["id"]]})
    assert r.status_code == 200


def test_3_preview_of_final_lesson(client):
    lid, _ = run(client, HTTP)
    html = client.get(f"/api/lessons/{lid}/preview").text
    assert "<h1>" in html and "References" in html


def test_3_export_finalized_lesson_references_and_qc_reports(client):
    lid, _ = run(client, HTTP)
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/lessons/{lid}/export?format=zip").content))
    names = {n.split("/", 1)[1] for n in z.namelist()}
    assert {"lesson.md", "lesson.html", "lesson.json", "references.md", "qc1_research_report.md",
            "qc2_instruction_report.md", "qc_reports.json"} <= names
    for fmt in ("md", "html", "json"):
        assert client.get(f"/api/lessons/{lid}/export?format={fmt}").status_code == 200


# ---------------------------------------------------------------- 4. Error handling
@pytest.mark.parametrize("payload", [{"title": "", "objective": "Explain things in detail"},
                                     {"title": "   ", "objective": "Explain things in detail"},
                                     {"title": "HTTP basics", "objective": ""},
                                     {"title": "HTTP basics"}])
def test_4_empty_inputs_are_rejected(client, payload):
    assert client.post("/api/lessons", json=payload).status_code == 422


def test_4_research_or_generation_failure_is_reported_and_retryable(client, monkeypatch):
    from app.pipeline import orchestrator
    monkeypatch.setattr(orchestrator, "AUTO_RETRY_WAIT", 0)
    real, calls = orchestrator.HANDLERS["research"], {"n": 0}

    def failing(lid):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("search engine unreachable")
        return real(lid)
    monkeypatch.setitem(orchestrator.HANDLERS, "research", failing)
    lid, d = run(client, HTTP)
    assert d["steps"]["research"]["status"] == "failed" and "unreachable" in d["steps"]["research"]["error"]
    assert d["steps"]["plan"]["status"] == "done"                     # earlier work kept
    client.post(f"/api/lessons/{lid}/retry", json={})
    for _ in range(300):
        d = client.get(f"/api/lessons/{lid}").json()
        if not d["running"] and all(s["status"] != "running" for s in d["steps"].values()):
            break
        time.sleep(0.05)
    assert all(s["status"] == "done" for s in d["steps"].values())


def test_4_saved_work_and_instructor_edits_are_preserved(client):
    lid, d = run(client, HTTP)
    content = d["version"]["content"]
    content["sections"][0]["content_md"] = "MY EDIT"
    v_edit = client.put(f"/api/lessons/{lid}/content", json={"content": content, "base_version": d["version"]["version"]}).json()["version"]
    # a stale save is refused instead of overwriting
    assert client.put(f"/api/lessons/{lid}/content", json={"content": content, "base_version": 1}).status_code == 409
    # regenerating the lesson adds a version; the instructor's version is untouched
    client.post(f"/api/lessons/{lid}/retry", json={"step": "generate"})
    for _ in range(300):
        d = client.get(f"/api/lessons/{lid}").json()
        if not d["running"] and all(s["status"] != "running" for s in d["steps"].values()):
            break
        time.sleep(0.05)
    assert d["version"]["version"] > v_edit
    assert client.get(f"/api/lessons/{lid}/versions/{v_edit}").json()["content"]["sections"][0]["content_md"] == "MY EDIT"
    assert client.post(f"/api/lessons/{lid}/versions/{v_edit}/restore").status_code == 200


# ---------------------------------------------------------------- Sample inputs
def test_sample_input_http_and_one_other_topic(client):
    for payload in (HTTP, SQL):
        _, d = run(client, payload)
        assert all(s["status"] == "done" for s in d["steps"].values())
        assert d["qc_research"] and d["qc_instruction"]
