"""Research step: plan queries -> search -> fetch -> score -> select -> chunk into evidence passages.

Output (persisted as the `research` step output):
  sources   : selected sources with id S1..Sn, type, credibility (tier + reason), relevance
  passages  : evidence chunks {id: "S2.p3", source_id, text} — the ONLY material the generator may cite
  warnings  : partial failures (e.g. YouTube unavailable) — research degrades gracefully
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import urlparse, urlunparse

from ..config import get_settings
from .books import search_books
from .credibility import score_web
from .text import chunk_text, term_coverage, tokenize
from .web import fetch_page, search_web
from .youtube import get_transcript, search_videos, search_videos_via_web

log = logging.getLogger("lessonforge.research")

MAX_WEB, MAX_BOOKS, MAX_VIDEOS = 6, 3, 4
MIN_WEB_WORDS = 150


class ResearchError(RuntimeError):
    pass


def _clean_query(q: str) -> str:
    """Remove web-search operators (site:, filetype:, quotes) that book/video search engines don't understand."""
    q = re.sub(r"\b(site|filetype|inurl|intitle):\S+", " ", q or "")
    q = q.replace('"', " ").replace("'", " ")
    return re.sub(r"\s+", " ", q).strip()


def _dedupe(items: list[str]) -> list[str]:
    out, seen = [], set()
    for x in items:
        k = (x if x.startswith("intitle:") else _clean_query(x)).lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


_GENERIC_TITLE = {"introduction", "intro", "basics", "fundamentals", "guide", "understanding", "overview", "lesson",
                  "requests", "responses", "methods", "vs", "and", "the", "of", "to", "in", "how", "what", "why"}


def _topic_tokens(title: str) -> list[str]:
    """Distinctive words of the lesson title, e.g. 'HTTP Requests and Responses' -> ['http'],
    'SQL Joins: INNER JOIN vs LEFT JOIN' -> ['sql', 'joins', ...]."""
    toks = [t for t in tokenize(title) if t not in _GENERIC_TITLE and not t.isdigit()]
    return list(dict.fromkeys(toks)) or tokenize(title)[:1]


def _simple_queries(title: str, plan: dict) -> list[str]:
    concepts = " ".join((plan.get("key_concepts") or [])[:2])
    base = re.sub(r"[:\-–—].*$", "", title).strip() or title
    return [f"{base} explained", base, f"{base} {concepts}".strip(), f"{base} tutorial"]


def _norm_url(u: str) -> str:
    p = urlparse(u)
    return urlunparse((p.scheme, p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", "", ""))


def _focus_terms(plan: dict, title: str, objective: str) -> list[str]:
    text = " ".join([title, objective, " ".join(plan.get("key_concepts", []))])
    return list(dict.fromkeys(tokenize(text)))


def collect(plan: dict, title: str, objective: str, emit: Callable[[str], None] = lambda m: None) -> dict:
    if get_settings().research_mode == "mock":
        from .mock_data import mock_research
        emit("Research mode is 'mock' — using offline fixture sources.")
        return mock_research(title, objective)

    q = plan.get("search_queries", {})
    terms = _focus_terms(plan, title, objective)
    short_terms = list(dict.fromkeys(tokenize(title + " " + objective)))  # for short texts (titles, blurbs)
    warnings: list[str] = []

    # ---------------------------------------------------------------- web
    raw: dict[str, dict] = {}

    def _run(queries: list[str]) -> None:
        for query in queries:
            try:
                for r in search_web(query, n=6):
                    raw.setdefault(_norm_url(r["url"]), r)
                emit(f"Web search: '{query}' → {len(raw)} unique results so far")
            except Exception as e:
                msg = f"Web search failed for '{query}': {str(e)[:120]}"
                warnings.append(msg)
                emit(msg)

    planned = _dedupe(q.get("web") or [title])[:4]
    _run(planned)
    # Planner queries are often narrow (site:edu, exact quotes) and a search engine may rate-limit some of
    # them. With too few results, widen the search with plain topic queries before giving up.
    if len(raw) < 8:
        emit(f"Only {len(raw)} results — widening the search with simpler queries")
        _run([x for x in _dedupe(_simple_queries(title, plan) + [_clean_query(x) for x in planned]) if x not in planned][:5])
    candidates = []
    for r in raw.values():
        cred = score_web(r["url"])
        pre = 0.5 * cred["score"] + 0.5 * term_coverage(terms, r["title"] + " " + r["snippet"])
        candidates.append({**r, "credibility": cred, "_pre": pre})
    candidates.sort(key=lambda x: x["_pre"], reverse=True)
    candidates = candidates[:14]

    def _fetch(c):
        if len((c.get("content") or "").split()) >= MIN_WEB_WORDS:
            return c
        try:
            c["content"] = fetch_page(c["url"])
        except Exception as e:
            c["content"] = ""
            c["_err"] = str(e)[:120]
        return c

    with ThreadPoolExecutor(max_workers=6) as ex:
        fetched = list(ex.map(_fetch, candidates))
    readable = [c for c in fetched if len(c["content"].split()) >= MIN_WEB_WORDS]
    emit(f"Fetched {len(fetched)} pages, {len(readable)} had readable content")
    for c in readable:
        c["relevance"] = round(term_coverage(terms, c["content"][:20000]), 2)
        c["_final"] = 0.5 * c["relevance"] + 0.5 * c["credibility"]["score"]
    readable.sort(key=lambda c: c["_final"], reverse=True)
    web, per_domain = [], {}
    for min_rel in (0.2, 0.0):  # relax the relevance floor if too few pages pass it
        for c in readable:  # domain diversity: at most 2 pages per site
            dom = urlparse(c["url"]).netloc
            if c in web or per_domain.get(dom, 0) >= 2 or c["relevance"] < min_rel:
                continue
            per_domain[dom] = per_domain.get(dom, 0) + 1
            web.append(c)
            if len(web) >= MAX_WEB:
                break
        if len(web) >= 3:
            break
    if len(web) < 2:
        raise ResearchError(
            f"Only {len(web)} relevant, readable web source(s) found — not enough to ground a lesson. "
            "Check your internet connection or add TAVILY_API_KEY, then click Retry."
        )

    # Book and video search engines return nothing for web-style queries (site:, quoted phrases), so each
    # source type tries progressively simpler queries until it finds something.
    fallback_queries = _simple_queries(title, plan)

    # -------------------------------------------------------------- books
    # Rank books by whether the lesson's main topic word is in the TITLE (a book about "Apache Solr" that mentions
    # HTTP requests in its blurb is not an HTTP book). Google Books supports intitle:, so ask for those directly.
    topic_tokens = _topic_tokens(title)
    book_queries = [f"intitle:{t}" for t in topic_tokens[:2]] + [_clean_query(x) for x in (q.get("books") or [])] + fallback_queries
    books: list[dict] = []
    seen_b: set = set()
    for query in _dedupe(book_queries)[:6]:
        try:
            for b in search_books(query if query.startswith("intitle:") else _clean_query(query), n=8):
                key = b["title"].lower()[:60]
                if key in seen_b or not b["title"]:
                    continue
                seen_b.add(key)
                in_title = any(t in set(tokenize(b["title"])) for t in topic_tokens)
                b["relevance"] = round(min(1.0, term_coverage(short_terms, b["title"] + " " + b["content"]) + (0.4 if in_title else 0)), 2)
                b["_in_title"] = in_title
                books.append(b)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Book search failed for '{query}': {e}")
        if sum(1 for b in books if b["_in_title"]) >= MAX_BOOKS:
            break
    books = [b for b in books if b["_in_title"] or b["relevance"] >= 0.3]   # never keep an off-topic book
    books.sort(key=lambda b: (b["_in_title"], 0.6 * b["relevance"] + 0.4 * b["credibility"]["score"]), reverse=True)
    books = books[:MAX_BOOKS]
    emit(f"Books: selected {len(books)}")
    if not books:
        warnings.append("No relevant books found for this topic (Google Books / Open Library).")

    # ------------------------------------------------------------- videos
    videos: list[dict] = []
    seen_v: set = set()
    for query in _dedupe([*(q.get("videos") or []), *fallback_queries])[:5]:
        try:
            found = search_videos(_clean_query(query), n=6)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Video search failed for '{query}': {e}")
            found = []
        if not found:   # second route: a normal web search restricted to youtube.com
            try:
                found = search_videos_via_web(_clean_query(query), n=6)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"YouTube web search failed for '{query}': {e}")
        for v in found:
            if v["video_id"] not in seen_v:
                seen_v.add(v["video_id"])
                videos.append(v)
        if len(videos) >= 6:
            break
    try:
        videos = videos[:10]
        with ThreadPoolExecutor(max_workers=5) as ex:
            transcripts = list(ex.map(lambda v: get_transcript(v["video_id"]), videos))
        for v, t in zip(videos, transcripts):
            v["content"] = t or ""
            v["has_transcript"] = bool(t)
            v["relevance"] = round(term_coverage(terms, t) if t else term_coverage(short_terms, v["title"] + " " + v.get("description", "")), 2)
            dur = v.get("duration_sec") or 0
            v["_fit"] = 1.0 if 180 <= dur <= 900 else (0.6 if dur <= 1500 else 0.2)  # 3–15 min ideal
        videos.sort(key=lambda v: 0.45 * v["relevance"] + 0.3 * v["credibility"]["score"] + 0.25 * v["_fit"], reverse=True)
        videos = ([v for v in videos if v["relevance"] >= 0.15] or videos[:2])[:MAX_VIDEOS]
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Video ranking failed: {e}")
    emit(f"YouTube: selected {len(videos)} videos ({sum(v.get('has_transcript', False) for v in videos)} with transcripts)")
    if not videos:
        warnings.append("No YouTube videos found for this topic.")

    return build_store(web, books, videos, warnings)


def build_store(web: list[dict], books: list[dict], videos: list[dict], warnings: list[str]) -> dict:
    sources, passages = [], []
    for item in web + books + videos:
        sid = f"S{len(sources) + 1}"
        content = item.get("content") or ""
        src = {
            "id": sid,
            "type": item.get("type", "web"),
            "title": (item.get("title") or "").strip()[:200],
            "url": item.get("url", ""),
            "snippet": (item.get("snippet") or item.get("description") or content[:240]).strip()[:300],
            "credibility": item["credibility"],
            "relevance": item.get("relevance", 0),
            "word_count": len(content.split()),
        }
        if src["type"] == "web":
            src["domain"] = urlparse(src["url"]).netloc
        for k in ("authors", "publisher", "year", "isbn", "channel", "duration_sec", "views", "published",
                  "video_id", "has_transcript"):
            if k in item:
                src[k] = item[k]
        sources.append(src)
        words = content.split()[:6000]
        for j, chunk in enumerate(chunk_text(" ".join(words)), start=1):
            passages.append({"id": f"{sid}.p{j}", "source_id": sid, "text": chunk})
    return {
        "sources": sources,
        "passages": passages,
        "warnings": warnings,
        "stats": {
            "web": len(web), "books": len(books), "videos": len(videos), "passages": len(passages),
            "authoritative": sum(1 for s in sources if s["credibility"]["tier"] == "authoritative"),
        },
    }
