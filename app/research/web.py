"""Web search + page extraction.

Search backends (first available wins):
  1. Tavily (TAVILY_API_KEY) — search API built for LLM research, returns page text.
  2. DuckDuckGo via the `ddgs` package — free, no key.
Page text is extracted with trafilatura (boilerplate removal) with a BeautifulSoup-free fallback.
"""
from __future__ import annotations

import logging
import re

import httpx

from ..config import get_settings

log = logging.getLogger("lessonforge.web")
def with_deadline(fn, seconds: float, *args, **kwargs):
    """Run fn in a daemon thread and stop waiting after `seconds` (some search libraries can hang, e.g. when
    the PC sleeps mid-request). Raises TimeoutError instead of blocking the pipeline; a daemon thread never
    delays Ctrl+C / server shutdown."""
    import queue
    import threading
    box: queue.Queue = queue.Queue(maxsize=1)

    def runner():
        try:
            box.put((True, fn(*args, **kwargs)))
        except BaseException as e:  # noqa: BLE001
            box.put((False, e))

    threading.Thread(target=runner, daemon=True).start()
    try:
        ok, value = box.get(timeout=max(0.1, seconds))
    except queue.Empty:
        raise TimeoutError(f"{getattr(fn, '__name__', 'request')} took longer than {seconds:.0f}s") from None
    if ok:
        return value
    raise value


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 LessonForge/1.0"


def search_web(query: str, n: int = 6) -> list[dict]:
    s = get_settings()
    if s.tavily_key:
        try:
            return _tavily(query, n, s.tavily_key)
        except Exception as e:  # fall back to DDG
            log.warning("Tavily failed (%s); falling back to DuckDuckGo", e)
    return _ddg(query, n)


def _tavily(query: str, n: int, key: str) -> list[dict]:
    r = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "max_results": n, "search_depth": "advanced", "include_raw_content": True},
        timeout=40,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", ""),
         "content": x.get("raw_content") or ""}
        for x in r.json().get("results", []) if x.get("url")
    ]


def _ddg(query: str, n: int) -> list[dict]:
    from ddgs import DDGS
    res = with_deadline(lambda: DDGS(timeout=15).text(query, max_results=n) or [], 45)
    return [{"title": x.get("title", ""), "url": x.get("href", ""), "snippet": x.get("body", ""), "content": ""}
            for x in res if x.get("href")]


def fetch_page(url: str, timeout: float | None = None) -> str:
    timeout = timeout or get_settings().http_timeout
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return ""
    html = r.text
    try:
        import trafilatura
        text = trafilatura.extract(html, include_tables=True, include_comments=False, favor_precision=True)
        if text and len(text.split()) > 80:
            return text
    except Exception as e:  # pragma: no cover
        log.debug("trafilatura failed on %s: %s", url, e)
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def check_link(url: str) -> dict:
    """Used by QC1 to detect dead references."""
    try:
        r = httpx.head(url, headers={"User-Agent": UA}, timeout=8, follow_redirects=True)
        if r.status_code in (403, 405, 501) or r.status_code >= 500:  # many sites block HEAD
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=10, follow_redirects=True)
        return {"ok": r.status_code < 400, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "status": None, "error": type(e).__name__}
