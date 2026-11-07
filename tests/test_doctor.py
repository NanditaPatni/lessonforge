"""Doctor: builds a working model chain from what the keys can actually use (network faked)."""
import httpx


def test_doctor_builds_chain_and_independent_qc(tmp_path, monkeypatch):
    import app.doctor as doc
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=g_test\nGROQ_API_KEY=gsk_test\n", encoding="utf-8")
    monkeypatch.setattr(doc, "ENV", env)
    monkeypatch.setattr("sys.argv", ["doctor"])
    monkeypatch.setattr(doc, "list_gemini", lambda k: ["gemini-flash-latest", "gemini-3.1-flash-lite", "gemini-2.5-flash"])
    monkeypatch.setattr(doc, "list_groq", lambda k: ["openai/gpt-oss-120b", "whisper-large-v3"])
    status = {"gemini-flash-latest": "busy", "gemini-2.5-flash": "bad", "gemini-3.1-flash-lite": "ok", "openai/gpt-oss-120b": "ok"}
    monkeypatch.setattr(doc, "list_gemini", lambda k: ["gemini-flash-latest", "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash"])
    status["gemini-3.5-flash"] = "ok"
    monkeypatch.setattr(doc, "probe", lambda p, m: (status[m], 1.0, ""))

    assert doc.main() == 0
    text = env.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=gemini" in text and "LLM_MODEL=gemini-3.5-flash" in text        # strongest working model, not fastest
    assert "QC_LLM_PROVIDER=groq" in text                                                  # independent QC
    assert "FALLBACK_LLM=gemini:gemini-3.1-flash-lite,groq:openai/gpt-oss-120b,gemini:gemini-flash-latest" in text  # busy last
    assert "gemini-2.5-flash" not in text.split("FALLBACK_LLM=")[1].split("\n")[0]         # broken model dropped
    assert "GROQ_API_KEY=gsk_test" in text
