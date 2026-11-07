"""Offline fixture research used when RESEARCH_MODE=mock (tests, demos without internet).

The text below is written for this project as a short summary of the referenced
standards/documentation; the URLs point to the real primary sources.
"""
from __future__ import annotations

from .collector import build_store
from .credibility import score_book, score_web

HTTP_WEB = [
    {
        "title": "HTTP request methods — MDN Web Docs",
        "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Methods",
        "content": (
            "HTTP defines a set of request methods that indicate the desired action to be performed on a resource. "
            "The GET method requests a representation of the specified resource. Requests using GET should only "
            "retrieve data and should not change server state. The POST method submits an entity to the specified "
            "resource, often causing a change in state or side effects on the server, such as creating a record or "
            "submitting a form. Other methods include PUT, which replaces the target resource with the request content, "
            "DELETE, which deletes the specified resource, PATCH, which applies partial modifications, and HEAD, which "
            "asks for a response identical to GET but without the response body. A method is safe if it does not alter "
            "the state of the server; GET, HEAD and OPTIONS are safe. A method is idempotent if making the same request "
            "several times has the same effect on the server as making it once; GET, HEAD, PUT and DELETE are idempotent "
            "while POST is neither safe nor idempotent. GET requests usually send parameters in the URL query string, "
            "for example /search?q=shoes, whereas POST requests usually carry data in the request body and describe "
            "its format with the Content-Type header such as application/json or application/x-www-form-urlencoded. "
            "Responses to GET are cacheable by default; responses to POST are only cacheable when explicit freshness "
            "information is included, which is rare in practice."
        ),
    },
    {
        "title": "HTTP response status codes — MDN Web Docs",
        "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status",
        "content": (
            "HTTP response status codes indicate whether a specific HTTP request has been successfully completed. "
            "Responses are grouped in five classes: informational responses (100–199), successful responses (200–299), "
            "redirection messages (300–399), client error responses (400–499) and server error responses (500–599). "
            "200 OK means the request succeeded. 201 Created means the request succeeded and a new resource was created "
            "as a result, typically the response sent after POST requests. 204 No Content means there is no content to "
            "send for this request. 400 Bad Request means the server cannot process the request due to something "
            "perceived to be a client error such as malformed request syntax. 401 Unauthorized means the client must "
            "authenticate itself to get the requested response. 403 Forbidden means the client does not have access "
            "rights to the content, and unlike 401 the client's identity is known to the server. 404 Not Found means "
            "the server cannot find the requested resource. 500 Internal Server Error means the server has encountered "
            "a situation it does not know how to handle. 502 Bad Gateway means the server, while working as a gateway, "
            "got an invalid response. 503 Service Unavailable means the server is not ready to handle the request, "
            "commonly because it is down for maintenance or overloaded."
        ),
    },
    {
        "title": "RFC 9110: HTTP Semantics",
        "url": "https://www.rfc-editor.org/rfc/rfc9110",
        "content": (
            "RFC 9110 defines the overall architecture and semantics of HTTP. A client sends a request message that "
            "begins with a method, a target URI and a protocol version, followed by header fields and optionally "
            "content. The server replies with a response message that begins with a status code and reason phrase, "
            "followed by header fields and optionally content. The status code is a three-digit integer; the first "
            "digit defines the class of response. The 2xx class indicates that the client's request was successfully "
            "received, understood and accepted. The 4xx class indicates that the client seems to have erred, and the "
            "server should include a representation explaining the error. The 5xx class indicates that the server is "
            "aware it has erred or is incapable of performing the requested method. Clients that do not recognise a "
            "status code must treat it as equivalent to the x00 code of its class. Content received in a GET request "
            "has no generally defined semantics and might lead some implementations to reject the request. The POST "
            "method requests that the target resource process the representation enclosed in the request according "
            "to the resource's own specific semantics. If one or more resources has been created on the origin "
            "server as a result of successfully processing a POST request, the origin server should send a 201 "
            "Created response containing a Location header field."
        ),
    },
]

HTTP_BOOKS = [
    {
        "type": "book",
        "title": "HTTP: The Definitive Guide",
        "url": "https://openlibrary.org/search?q=HTTP+The+Definitive+Guide",
        "authors": ["David Gourley", "Brian Totty"],
        "publisher": "O'Reilly Media",
        "year": 2002,
        "content": "A comprehensive guide to the HTTP protocol covering request and response messages, methods, "
                   "status codes, headers, caching, proxies and connection management.",
    }
]


def _generic_web(title: str, objective: str) -> list[dict]:
    return [{
        "title": f"Offline mock source for '{title}'",
        "url": "https://example.org/mock-source",
        "content": f"This offline placeholder source exists only so the pipeline can run without internet. "
                   f"Topic: {title}. Objective: {objective}. Switch RESEARCH_MODE to live for real research. " * 3,
    }, {
        "title": "Second offline mock source",
        "url": "https://example.org/mock-source-2",
        "content": f"Additional placeholder text about {title}. {objective}. " * 6,
    }]


def mock_research(title: str, objective: str) -> dict:
    is_http = "http" in (title + objective).lower()
    web_items = HTTP_WEB if is_http else _generic_web(title, objective)
    web = [{**w, "type": "web", "snippet": w["content"][:240], "credibility": score_web(w["url"]), "relevance": 0.9}
           for w in web_items]
    books = [{**b, "credibility": score_book(b["publisher"], b["year"]), "relevance": 0.8}
             for b in (HTTP_BOOKS if is_http else [])]
    videos = [{
        "type": "video", "video_id": "", "title": f"YouTube search: {title} (offline mock)",
        "url": f"https://www.youtube.com/results?search_query={title.replace(' ', '+')}",
        "channel": "Offline mock", "duration_sec": 420, "views": 0, "has_transcript": False,
        "content": "", "credibility": {"score": 0.5, "tier": "general", "reason": "Mock placeholder"}, "relevance": 0.5,
    }]
    store = build_store(web, books, videos, ["Running in mock research mode (offline fixtures)."])
    store["mock"] = True
    return store
