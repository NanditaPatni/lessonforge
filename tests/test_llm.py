"""LLM client: automatic fallback + Groq strict-JSON retry (network faked)."""
import json

import httpx


def _resp(url, status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def test_groq_413_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    monkeypatch.setenv("FALLBACK_LLM", "gemini:gemini-flash-latest")
    calls = []

    def fake_post(self, url, json=None, headers=None, **kw):
        calls.append(url)
        if "groq" in url:
            return _resp(url, 413, {"error": {"message": "Request too large"}})
        return _resp(url, 200, {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    from app.llm import LLMClient
    c = LLMClient()
    assert c.json("sys", "user", task="t") == {"ok": True}
    assert any("groq" in u for u in calls) and any("gemini-flash-latest" in u for u in calls)
    assert c.fallback_used and "fallback gemini" in c.label
    assert c.provider == "groq"  # primary restored for the next call


def test_groq_json_validate_failed_retries_without_json_mode(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("FALLBACK_LLM", raising=False)
    bodies = []

    def fake_post(self, url, json=None, headers=None, **kw):
        bodies.append(dict(json))
        if "response_format" in json:
            return _resp(url, 400, {"error": {"code": "json_validate_failed"}})
        return _resp(url, 200, {"choices": [{"message": {"content": 'Here: {"ok": 1}'}}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    from app.llm import LLMClient
    assert LLMClient().json("sys", "user", task="t") == {"ok": 1}
    assert bodies[0]["reasoning_effort"] == "low" and "response_format" not in bodies[1]


def test_unusable_json_hands_task_to_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    monkeypatch.setenv("FALLBACK_LLM", "gemini:gemini-flash-latest")

    def fake_post(self, url, json=None, headers=None, **kw):
        if "groq" in url:   # groq keeps answering with prose, never JSON
            return _resp(url, 200, {"choices": [{"message": {"content": "Sure! Here is your lesson about HTTP."}, "finish_reason": "stop"}]})
        return _resp(url, 200, {"candidates": [{"content": {"parts": [{"text": '{"sections": [1, 2, 3]}'}]}}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    from app.llm import LLMClient
    c = LLMClient()
    assert c.json("sys", "user", task="generate") == {"sections": [1, 2, 3]}
    assert c.fallback_used


def test_empty_groq_reply_triggers_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    monkeypatch.setenv("FALLBACK_LLM", "gemini:gemini-flash-latest")

    def fake_post(self, url, json=None, headers=None, **kw):
        if "groq" in url:
            return _resp(url, 200, {"choices": [{"message": {"content": None}, "finish_reason": "length"}]})
        return _resp(url, 200, {"candidates": [{"content": {"parts": [{"text": '{"ok": 2}'}]}}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    from app.llm import LLMClient
    assert LLMClient().json("sys", "user", task="t") == {"ok": 2}


def test_fallback_chain_tries_second_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    monkeypatch.setenv("FALLBACK_LLM", "gemini:gemini-flash-latest,gemini:gemini-3.1-flash-lite")
    monkeypatch.setattr("time.sleep", lambda s: None)

    def fake_post(self, url, json=None, headers=None, **kw):
        if "groq" in url:
            return _resp(url, 413, {"error": {"message": "too large"}})
        if "flash-latest" in url:
            return _resp(url, 503, {"error": {"message": "high demand"}})
        return _resp(url, 200, {"candidates": [{"content": {"parts": [{"text": '{"ok": 3}'}]}, "finishReason": "STOP"}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    from app.llm import LLMClient
    c = LLMClient()
    assert c.json("sys", "user", task="t") == {"ok": 3}
    assert "flash-lite" in c.label


def test_extract_json_with_code_blocks_inside_content():
    """Regression: a section whose content contains a ```http code block must parse (it used to fail)."""
    from app.llm import extract_json
    reply = '{\n  "id": "sec-4",\n  "type": "example",\n  "content_md": "Response:\\n```http\\nHTTP/1.1 404 Not Found\\n```\\nThat is a 4xx."\n}'
    assert extract_json(reply)["id"] == "sec-4"
    assert "404" in extract_json("```json\n" + reply + "\n```")["content_md"]          # fenced whole reply
    assert extract_json("Here you go:\n" + reply + "\nHope this helps")["type"] == "example"  # prose around it
    assert extract_json('[{"a": 1}]') == [{"a": 1}]


def test_daily_quota_moves_to_fallback_without_retry_waits(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("FALLBACK_LLM", "groq:openai/gpt-oss-120b")
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    calls = []

    def fake_post(self, url, json=None, headers=None, **kw):
        calls.append(url)
        if "gemini" in url:
            return _resp(url, 429, {"error": {"code": 429, "message": "You exceeded your current quota",
                                              "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
        return _resp(url, 200, {"choices": [{"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)

    from app.llm import LLMClient
    assert LLMClient().json("s", "u", task="t") == {"ok": 1}
    assert sum("gemini" in u for u in calls) == 1 and not slept     # no pointless retries on a daily quota
