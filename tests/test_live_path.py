"""Exercises the LIVE code path (real provider adapter, real research collector, second-opinion
re-search, link checks) with the network faked out. Also proves QC1 catches a planted factual error.
"""
from __future__ import annotations

import json
import re

import httpx
import pytest

LESSON = {
    "title": "HTTP Requests and Responses", "objective": "obj", "audience": "beginners", "prerequisites": [],
    "sections": [
        {"id": "sec-1", "type": "hook", "heading": "Hook", "objectives": "LO1", "content_md": "Browsers send requests [S1]."},
        {"id": "sec-2", "type": "concept", "heading": "GET", "objectives": ["LO1"],
         "content_md": "GET retrieves a representation of a resource [S1]. GET requests are not idempotent [S1]."},
        {"id": "sec-3", "type": "concept", "heading": "Status", "objectives": ["LO2"],
         "content_md": "5xx codes mean the server failed [S2][S77]. A 418 code proves the server is a teapot."},
        {"id": "sec-4", "type": "activity", "heading": "Do it", "objectives": ["LO1", "LO2"], "activity_minutes": 15,
         "content_md": "Open DevTools and classify requests by status class; compare GET vs POST."},
        {"id": "sec-5", "type": "summary", "heading": "Summary", "objectives": ["LO1", "LO2"], "content_md": "Recap [S1][S2]."},
    ],
    "quiz": [{"id": f"Q{i}", "question": f"Question {i}?", "options": ["a", "bb", "cc", "dd"], "answer_index": "B" if i % 2 else 2,
              "explanation": "because [S1]", "objective": "LO1" if i < 3 else "LO2"} for i in range(1, 6)],
    "videos": [{"source_id": "S4", "placement": "optional", "why": "visual"}],
}


def fake_llm_reply(system: str, user: str) -> dict:
    if "Decompose the objective" in user:
        return {"audience": "beginners", "prerequisites": [], "key_concepts": ["GET", "status code"], "common_misconceptions": [],
                "sub_objectives": [{"text": "Explain GET"}, {"text": "Distinguish status classes"}],
                "search_queries": {"web": ["http get", "http status"], "books": ["http book"], "videos": ["http video"]}}
    if "WRITING RULES" in user:
        assert "[S1.p1]" in user, "generator must receive evidence passages"
        return LESSON
    if "Design the lesson OUTLINE" in user:       # chunked generation, call 1
        assert "[S1.p1]" in user, "outline must receive evidence passages"
        out = json.loads(json.dumps(LESSON))
        for sec in out["sections"]:
            sec["brief"] = sec.pop("content_md")[:40]
        return out
    if "Write the content for ONLY these sections" in user:   # chunked generation, calls 2..n
        ids = re.findall(r"- \[(sec-\d+)\]", user)
        by_id = {x["id"]: x["content_md"] for x in LESSON["sections"]}
        return {"sections": [{"id": i, "content_md": by_id[i]} for i in ids]}
    if "does not fit its" in user:
        return LESSON
    if "Extract the factual claims" in user:
        return {"claims": [
            {"id": "C1", "section_id": "sec-2", "text": "GET retrieves a representation", "quote": "GET retrieves a representation of a resource [S1].", "importance": "high"},
            {"id": "C2", "section_id": "sec-2", "text": "GET is not idempotent", "quote": "GET requests are not idempotent", "importance": "high"},
            {"id": "C3", "section_id": "sec-3", "text": "418 proves a teapot", "quote": "A 418 code proves the server is a teapot.", "importance": "low"}]}
    if "For each claim below" in user:
        ids = re.findall(r"### (C\d+)", user)
        out = []
        for cid in ids:
            if cid == "C1":
                out.append({"id": cid, "verdict": "supported", "evidence_ids": ["S1.p1"], "knowledge_check": "correct", "explanation": "ok", "correction": ""})
            elif cid == "C2":
                out.append({"id": cid, "verdict": "contradicted", "evidence_ids": ["S1.p1"], "knowledge_check": "incorrect",
                            "explanation": "GET is idempotent.", "correction": "GET requests are idempotent"})
            else:  # unsupported in original research; second-opinion page supports it
                v = "supported" if "teapot" in user and "RFC 2324" in user else "unsupported"
                out.append({"id": cid, "verdict": v, "evidence_ids": [], "knowledge_check": "uncertain", "explanation": "x", "correction": ""})
        return {"results": out}
    if "Audit this lesson" in user:
        return {"issues": []}
    if "Score each criterion" in user:
        return {"scores": {"clarity": {"score": 4, "justification": "clear"}}, "strengths": ["s"], "issues": []}
    raise AssertionError("unexpected prompt: " + user[:200])


@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "live.db"))
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("RESEARCH_MODE", "live")
    monkeypatch.delenv("QC_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    def fake_post(self, url, json=None, headers=None, **kw):  # Gemini REST endpoint
        assert "generativelanguage.googleapis.com" in url and headers["x-goog-api-key"] == "test-key"
        user = json["contents"][0]["parts"][0]["text"]
        body = {"candidates": [{"content": {"parts": [{"text": "```json\n" + __import__("json").dumps(fake_llm_reply("", user)) + "\n```"}]}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    page = "HTTP GET method retrieves a representation of the target resource. GET is safe and idempotent. " * 20
    status = "Status codes: 2xx success, 4xx client error, 5xx server error means the server failed. " * 20
    teapot = "RFC 2324 defines status 418 I'm a teapot as an April Fools joke for coffee pots. " * 20

    def fake_search(q, n=6):
        if "418" in q or "teapot" in q:
            return [{"title": "RFC 2324", "url": "https://www.rfc-editor.org/rfc/rfc2324", "snippet": "", "content": teapot}]
        return [{"title": "MDN GET", "url": "https://developer.mozilla.org/get", "snippet": "GET", "content": page},
                {"title": "MDN Status", "url": "https://developer.mozilla.org/status", "snippet": "status", "content": status},
                {"title": "Some blog", "url": "https://medium.com/x", "snippet": "http", "content": "too short"}]
    import app.research.collector as col
    import app.research.web as web
    monkeypatch.setattr(col, "search_web", fake_search)
    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "check_link", lambda url: {"ok": "medium" not in url, "status": 200})
    monkeypatch.setattr(col, "search_books", lambda q, n=6: [{"type": "book", "title": "HTTP: The Definitive Guide", "url": "https://books/x",
        "authors": ["Gourley"], "publisher": "O'Reilly", "year": 2002, "content": "HTTP status GET", "credibility": {"score": 0.8, "tier": "reputable", "reason": "r"}}])
    monkeypatch.setattr(col, "search_videos", lambda q, n=6: [{"type": "video", "video_id": "abcdefghijk", "title": "HTTP explained",
        "url": "https://www.youtube.com/watch?v=abcdefghijk", "channel": "freeCodeCamp.org", "duration_sec": 400, "views": 10, "description": "",
        "credibility": {"score": 0.8, "tier": "reputable", "reason": "r"}}])
    monkeypatch.setattr(col, "get_transcript", lambda vid: "in this video http get status codes " * 30)


def test_live_pipeline_catches_planted_error(live_env):
    from app import db
    from app.pipeline import orchestrator
    from app.pipeline.apply import apply_issues
    from app.llm import LLMClient
    db.init()
    lid = db.create_lesson("HTTP Requests and Responses", "Explain GET and distinguish 2xx, 4xx and 5xx status classes")
    orchestrator.start(lid, background=False)
    steps = db.get_steps(lid)
    assert all(s["status"] == "done" for s in steps.values()), {k: v["error"] for k, v in steps.items()}

    research = steps["research"]["output"]
    assert [s["type"] for s in research["sources"]] == ["web", "web", "book", "video"]  # short blog page dropped

    lesson = db.get_version(lid)["content"]
    assert "[S77]" not in lesson["sections"][2]["content_md"]          # hallucinated citation stripped
    assert lesson["quiz"][0]["answer_index"] == 1                       # "B" normalised
    assert lesson["sections"][0]["objectives"] == ["LO1"]               # string normalised

    qc1 = steps["qc_research"]["output"]
    crit = [i for i in qc1["issues"] if i["severity"] == "critical"]
    assert crit and crit[0]["fix"]["op"] == "replace_text" and "not idempotent" in crit[0]["fix"]["find"]
    c3 = next(c for c in qc1["claims"] if c["id"] == "C3")
    assert c3["second_opinion"] and c3["second_opinion"]["verdict"] == "supported"   # independent re-search
    c2 = next(c for c in qc1["claims"] if c["id"] == "C2")
    assert c2["second_opinion"]["verdict"] == "contradicted"                       # error independently confirmed
    assert qc1["metrics"]["independently_verified"] == 2 and qc1["status"] == "fail"

    # apply the deterministic fix + the citation from the independent source
    chosen = [i for i in qc1["issues"] if i["fix"]["op"] in ("replace_text", "add_citation")]
    new, results = apply_issues(lesson, chosen, research, LLMClient())
    assert all(r["ok"] for r in results), results
    sec2 = next(s for s in new["sections"] if s["id"] == "sec-2")["content_md"]
    assert "GET requests are idempotent" in sec2 and "not idempotent" not in sec2
    sec3 = next(s for s in new["sections"] if s["id"] == "sec-3")
    added = next(i for i in chosen if i["fix"]["op"] == "add_citation")["fix"]["source"]
    assert added["id"] in sec3["citations"] and added["url"].endswith("rfc2324")
