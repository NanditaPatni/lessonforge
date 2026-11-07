"""LessonForge doctor — finds which AI models your keys can use RIGHT NOW and configures .env.

    python -m app.doctor            # interactive: asks for missing keys, tests models, writes .env
    python -m app.doctor --check    # only test and report, change nothing

Why: free-tier models come and go (retired names -> 404, overloaded -> 503, small limits -> 413).
Instead of guessing a model name, the doctor probes candidates with a tiny JSON request and writes
an ordered chain: the fastest working model becomes LLM_PROVIDER/LLM_MODEL, the rest become
FALLBACK_LLM, and — when two providers work — QC is assigned to the *other* provider so the
review is independent.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx
from dotenv import dotenv_values

from .config import ROOT

ENV = ROOT / ".env"

# Preference order. Only names your key actually lists are tried.
# Strongest first; "lite" / small models are kept only as fallbacks.
GEMINI_CANDIDATES = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3-flash-preview", "gemini-2.5-flash",
                     "gemini-2.5-pro", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]
GROQ_CANDIDATES = ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905", "moonshotai/kimi-k2-instruct",
                   "meta-llama/llama-4-maverick-17b-128e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct",
                   "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "qwen/qwen3-32b"]

G, R, Y, B, X = "\033[92m", "\033[91m", "\033[93m", "\033[1m", "\033[0m"
if os.name == "nt":
    os.system("")  # enable ANSI colours in the Windows console


def ask_key(name: str, hint: str, current: str) -> str:
    if current:
        return current
    v = input(f"Paste your {name} (or press Enter to skip) [{hint}]: ").strip().strip('"').strip("'")
    return v


def list_gemini(key: str) -> list[str]:
    r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 200}, timeout=20)
    r.raise_for_status()
    return [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def list_groq(key: str) -> list[str]:
    r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def probe(provider: str, model: str) -> tuple[str, float, str]:
    """Return (status, seconds, detail). status: ok | busy | bad"""
    from .llm import LLMClient, LLMError
    c = LLMClient()
    c.provider, c.model = provider, model
    c.settings.fallbacks = []
    t0 = time.time()
    try:
        raw = c._complete_once("You reply with JSON only.", 'Return exactly {"ok": true, "n": 3}', 0.0, True, max_attempts=1)
        from .llm import extract_json
        d = extract_json(raw)
        if d.get("ok") is True:
            return "ok", time.time() - t0, ""
        return "bad", time.time() - t0, f"unexpected reply {raw[:60]!r}"
    except LLMError as e:
        msg = str(e)
        busy = any(code in msg for code in ("HTTP 503", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 504", "HTTP 529", "Could not reach"))
        return ("busy" if busy else "bad"), time.time() - t0, msg[:110]
    except Exception as e:  # noqa: BLE001
        return "bad", time.time() - t0, f"{type(e).__name__}: {e}"[:110]


def write_env(values: dict[str, str]) -> None:
    order = ["LLM_PROVIDER", "LLM_MODEL", "FALLBACK_LLM", "QC_LLM_PROVIDER", "QC_LLM_MODEL", "GEMINI_API_KEY",
             "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "TAVILY_API_KEY",
             "YOUTUBE_API_KEY", "RESEARCH_MODE", "GENERATION_MODE", "LESSON_MINUTES", "READING_WPM", "SPEAKING_WPM",
             "CHECK_LINKS"]
    lines = ["# Written by `python -m app.doctor` — re-run it any time a model stops working.", ""]
    for k in order + sorted(k for k in values if k not in order):
        v = values.get(k)
        if v not in (None, ""):
            lines.append(f"{k}={v}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Find working models and configure .env")
    ap.add_argument("--check", action="store_true", help="only test and report; do not change .env")
    a = ap.parse_args()

    vals = {k: (v or "").strip() for k, v in dotenv_values(ENV).items()} if ENV.exists() else {}
    print(f"{B}LessonForge doctor{X}  ({ENV})\n")
    if not a.check:
        vals["GEMINI_API_KEY"] = ask_key("Gemini API key", "free: https://aistudio.google.com/apikey", vals.get("GEMINI_API_KEY", ""))
        vals["GROQ_API_KEY"] = ask_key("Groq API key", "free: https://console.groq.com/keys, starts with gsk_", vals.get("GROQ_API_KEY", ""))
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        if vals.get(k):
            os.environ[k] = vals[k]
    os.environ.pop("FALLBACK_LLM", None)

    found: list[tuple[str, str]] = []
    if vals.get("GEMINI_API_KEY"):
        try:
            avail = set(list_gemini(vals["GEMINI_API_KEY"]))
            found += [("gemini", m) for m in GEMINI_CANDIDATES if m in avail]
            print(f"Gemini key {G}valid{X} — {len(avail)} models available")
        except httpx.HTTPStatusError as e:
            print(f"Gemini key {R}rejected{X} (HTTP {e.response.status_code}) — check the key")
        except Exception as e:  # noqa: BLE001
            print(f"Gemini {Y}unreachable{X}: {e}")
    if vals.get("GROQ_API_KEY"):
        try:
            avail = set(list_groq(vals["GROQ_API_KEY"]))
            found += [("groq", m) for m in GROQ_CANDIDATES if m in avail]
            print(f"Groq key {G}valid{X} — {len(avail)} models available")
        except httpx.HTTPStatusError as e:
            print(f"Groq key {R}rejected{X} (HTTP {e.response.status_code}) — check the key (it must start with gsk_)")
        except Exception as e:  # noqa: BLE001
            print(f"Groq {Y}unreachable{X}: {e}")
    if not found:
        print(f"\n{R}No usable models.{X} Add at least one valid key and run this again.")
        return 1

    print(f"\nTesting {len(found)} models with a tiny request (about 1 minute)…")
    ok, busy = [], []
    for prov, model in found:
        status, secs, detail = probe(prov, model)
        colour = {"ok": G, "busy": Y, "bad": R}[status]
        print(f"  {colour}{status.upper():4}{X}  {prov}:{model:45s} {secs:5.1f}s  {detail}")
        (ok if status == "ok" else busy if status == "busy" else []).append((prov, model, secs))

    if not ok and not busy:
        print(f"\n{R}None of the models answered correctly.{X} See the messages above.")
        return 1
    # Rank by model QUALITY (candidate order), not speed: Gemini first (large token limits), strongest model first.
    # Busy models go last (they often recover). Lite/small models end up as fallbacks.
    rank = {("gemini", m): i for i, m in enumerate(GEMINI_CANDIDATES)} | {("groq", m): i for i, m in enumerate(GROQ_CANDIDATES)}
    ok.sort(key=lambda x: (x[0] != "gemini", rank.get((x[0], x[1]), 99)))
    chain = [(p, m) for p, m, _ in ok] + [(p, m) for p, m, _ in busy]
    primary, rest = chain[0], chain[1:5]
    qc = next(((p, m) for p, m in chain if p != primary[0]), None)

    print(f"\n{B}Recommended configuration{X}")
    print(f"  Generator : {primary[0]}:{primary[1]}")
    print(f"  QC review : {(qc or primary)[0]}:{(qc or primary)[1]}" + ("  (different provider → independent review)" if qc else ""))
    print(f"  Fallbacks : {', '.join(f'{p}:{m}' for p, m in rest) or 'none'}")
    if not ok:
        print(f"  {Y}All models are busy right now — the app will keep retrying, or run the doctor again in a few minutes.{X}")

    if a.check:
        return 0
    vals.update({
        "LLM_PROVIDER": primary[0], "LLM_MODEL": primary[1],
        "FALLBACK_LLM": ",".join(f"{p}:{m}" for p, m in rest),
        "QC_LLM_PROVIDER": qc[0] if qc else "", "QC_LLM_MODEL": qc[1] if qc else "",
    })
    vals.setdefault("RESEARCH_MODE", "live")
    vals.setdefault("GENERATION_MODE", "chunked")
    vals.setdefault("LESSON_MINUTES", "45")
    vals.setdefault("CHECK_LINKS", "true")
    write_env(vals)
    print(f"\n{G}Saved to .env.{X} Start the app with:  python -m uvicorn app.main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
