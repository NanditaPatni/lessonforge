"""Book discovery via Google Books (no key needed) with Open Library as fallback.

Full book text is not freely available, so books contribute *metadata + publisher
descriptions* as low-weight evidence and appear as recommended further reading.
This limitation is surfaced in the research log rather than hidden.
"""
from __future__ import annotations

import re

import httpx

from .credibility import score_book


def _year(s: str | None) -> int | None:
    m = re.search(r"\d{4}", s or "")
    return int(m.group()) if m else None


def search_books(query: str, n: int = 5) -> list[dict]:
    try:
        res = _google_books(query, n)
        if res:
            return res
    except Exception:
        pass
    return _open_library(query, n)   # also used when Google Books returns nothing


def _google_books(query: str, n: int) -> list[dict]:
    r = httpx.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": query, "maxResults": n, "printType": "books", "orderBy": "relevance", "langRestrict": "en"},
        timeout=20,
    )
    r.raise_for_status()
    out = []
    for item in r.json().get("items", []):
        v = item.get("volumeInfo", {})
        desc = re.sub(r"<[^>]+>", " ", v.get("description", "") or "")
        snippet = re.sub(r"<[^>]+>", " ", (item.get("searchInfo") or {}).get("textSnippet", "") or "")
        isbn = next((i["identifier"] for i in v.get("industryIdentifiers", []) if i.get("type") == "ISBN_13"), "")
        year = _year(v.get("publishedDate"))
        out.append({
            "type": "book",
            "title": v.get("title", "") + (f": {v['subtitle']}" if v.get("subtitle") else ""),
            "url": v.get("infoLink") or v.get("canonicalVolumeLink") or "",
            "authors": v.get("authors", []),
            "publisher": v.get("publisher", ""),
            "year": year,
            "isbn": isbn,
            "content": (desc + " " + snippet).strip(),
            "credibility": score_book(v.get("publisher", ""), year),
        })
    return out


def _open_library(query: str, n: int) -> list[dict]:
    r = httpx.get("https://openlibrary.org/search.json", params={"q": query, "limit": n}, timeout=20)
    r.raise_for_status()
    out = []
    for d in r.json().get("docs", []):
        pub = (d.get("publisher") or [""])[0]
        year = d.get("first_publish_year")
        out.append({
            "type": "book",
            "title": d.get("title", ""),
            "url": f"https://openlibrary.org{d.get('key', '')}",
            "authors": d.get("author_name", [])[:3],
            "publisher": pub,
            "year": year,
            "isbn": (d.get("isbn") or [""])[0],
            "content": " ".join(d.get("subject", [])[:15]),
            "credibility": score_book(pub, year),
        })
    return out
