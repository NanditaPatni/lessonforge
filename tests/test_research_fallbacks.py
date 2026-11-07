"""Regression for the 11:44 run: planner wrote site:-restricted queries, Google Books returned nothing and
video search returned nothing -> lesson had no books and no videos. Research must fall back and still find both."""
import app.research.books as books_mod
import app.research.collector as col
import app.research.web as web


def test_books_and_videos_found_despite_narrow_queries(monkeypatch):
    monkeypatch.setenv("RESEARCH_MODE", "live")
    page = "HTTP GET POST methods status codes 2xx 4xx 5xx client server error request response " * 30
    queries_seen = {"books": [], "videos": []}

    def fake_web(q, n=6):
        if "site:youtube.com" in q:
            return [{"title": "HTTP Status Codes Explained - YouTube", "url": "https://www.youtube.com/watch?v=abcdefghijk",
                     "snippet": "GET POST 2xx 4xx 5xx", "content": ""}]
        return [{"title": f"Doc {i}", "url": f"https://developer.mozilla.org/page{i}", "snippet": "HTTP", "content": page}
                for i in range(3)]

    def fake_books(q, n=6):
        queries_seen["books"].append(q)
        if "site:" in q or '"' in q:
            raise AssertionError("book search must receive cleaned queries")
        if q.endswith("explained"):
            return []                                  # first attempt: nothing
        return [{"type": "book", "title": "HTTP: The Definitive Guide", "url": "https://openlibrary.org/x", "authors": ["Gourley"],
                 "publisher": "O'Reilly", "year": 2002, "content": "HTTP methods status codes",
                 "credibility": {"score": 0.8, "tier": "reputable", "reason": "r"}}]

    def fake_videos(q, n=6):
        queries_seen["videos"].append(q)
        return []                                      # video search finds nothing at all

    monkeypatch.setattr(col, "search_web", fake_web)
    monkeypatch.setattr(web, "search_web", fake_web)
    monkeypatch.setattr(col, "search_books", fake_books)
    monkeypatch.setattr(col, "search_videos", fake_videos)
    monkeypatch.setattr(col, "get_transcript", lambda vid: None)

    plan = {"key_concepts": ["status code"], "search_queries": {
        "web": ['"HTTP GET method" site:w3.org'], "books": ['"HTTP status code categories" site:ietf.org'],
        "videos": ['"HTTP" "official documentation" site:ietf.org']}}
    res = col.collect(plan, "HTTP Requests and Responses", "Explain GET and POST and 2xx 4xx 5xx")
    types = [s["type"] for s in res["sources"]]
    assert "book" in types, res["warnings"]
    assert "video" in types, res["warnings"]
    assert all("site:" not in q for q in queries_seen["books"] + queries_seen["videos"])


def test_open_library_used_when_google_books_is_empty(monkeypatch):
    monkeypatch.setattr(books_mod, "_google_books", lambda q, n: [])
    monkeypatch.setattr(books_mod, "_open_library", lambda q, n: [{"title": "OL book"}])
    assert books_mod.search_books("http") == [{"title": "OL book"}]


def test_off_topic_books_are_dropped_and_title_matches_preferred(monkeypatch):
    monkeypatch.setenv("RESEARCH_MODE", "live")
    page = "HTTP GET POST methods status codes 2xx 4xx 5xx client server error request response " * 30
    monkeypatch.setattr(col, "search_web", lambda q, n=6: [{"title": f"D{i}", "url": f"https://developer.mozilla.org/p{i}",
                                                            "snippet": "HTTP", "content": page} for i in range(3)])
    monkeypatch.setattr(col, "search_videos", lambda q, n=6: [])
    monkeypatch.setattr(col, "search_videos_via_web", lambda q, n=6: [])
    monkeypatch.setattr(col, "get_transcript", lambda v: None)
    asked = []

    def fake_books(q, n=6):
        asked.append(q)
        solr = {"type": "book", "title": "Apache Solr Beginner's Guide", "url": "u1", "authors": [], "publisher": "",
                "year": 2013, "content": "search server", "credibility": {"score": 0.6, "tier": "general", "reason": "r"}}
        http = {"type": "book", "title": "HTTP: The Definitive Guide", "url": "u2", "authors": [], "publisher": "O'Reilly",
                "year": 2002, "content": "protocol", "credibility": {"score": 0.8, "tier": "reputable", "reason": "r"}}
        return [solr, http] if q.startswith("intitle:") else [solr]

    monkeypatch.setattr(col, "search_books", fake_books)
    res = col.collect({"search_queries": {"web": ["http"]}}, "HTTP Requests and Responses", "Explain GET and POST")
    titles = [s["title"] for s in res["sources"] if s["type"] == "book"]
    assert titles == ["HTTP: The Definitive Guide"] and asked[0] == "intitle:http"
