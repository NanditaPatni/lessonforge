"""Deterministic offline stand-in for an LLM (LLM_PROVIDER=mock).

Used by the test-suite and for UI demos without API keys. It returns schema-valid
responses for every pipeline task. The HTTP lesson content is hand-written and accurate
so the offline demo is meaningful; other topics get a generic template.
"""
from __future__ import annotations

import copy
import re

from .research.text import tokenize


def respond(task: str, ctx: dict):
    fn = globals().get(f"_{task}")
    if not fn:
        raise ValueError(f"mock LLM has no handler for task '{task}'")
    return fn(ctx)


def _is_http(ctx) -> bool:
    return "http" in (ctx.get("title", "") + ctx.get("objective", "")).lower()


# --------------------------------------------------------------------- plan
def _plan(ctx):
    if _is_http(ctx):
        return {
            "audience": "Beginner web / backend developers who have used a browser and written basic code",
            "prerequisites": ["What a URL is", "Basic client-server idea"],
            "sub_objectives": [
                {"id": "LO1", "text": "Explain what a GET request does and when to use it", "bloom": "understand",
                 "evidence_of_mastery": "Describes GET semantics (safe, idempotent, params in URL)"},
                {"id": "LO2", "text": "Explain what a POST request does and how it differs from GET", "bloom": "understand",
                 "evidence_of_mastery": "Describes POST semantics (body, side effects, not idempotent)"},
                {"id": "LO3", "text": "Distinguish 2xx, 4xx and 5xx response status categories", "bloom": "analyze",
                 "evidence_of_mastery": "Classifies a status code and says who must act"},
            ],
            "key_concepts": ["request method", "status code", "idempotent", "safe method", "request body", "query string",
                             "client error", "server error"],
            "common_misconceptions": ["GET and POST differ only in where data goes", "A 404 means the server is broken",
                                      "Any 2xx means a resource was created"],
            "search_queries": {"web": ["HTTP GET vs POST methods", "HTTP response status codes classes", "RFC 9110 HTTP semantics"],
                               "books": ["HTTP definitive guide"], "videos": ["HTTP requests and responses explained"]},
        }
    parts = [p.strip() for p in re.split(r",| and ", ctx.get("objective", "")) if len(p.strip()) > 3][:4] or [ctx.get("objective", "")]
    return {
        "audience": "Beginner learners", "prerequisites": [],
        "sub_objectives": [{"id": f"LO{i + 1}", "text": p[0].upper() + p[1:], "bloom": "understand",
                            "evidence_of_mastery": f"Can explain: {p}"} for i, p in enumerate(parts)],
        "key_concepts": tokenize(ctx.get("title", ""))[:5], "common_misconceptions": [],
        "search_queries": {"web": [ctx.get("title", "")], "books": [ctx.get("title", "")], "videos": [ctx.get("title", "")]},
    }


# ----------------------------------------------------------------- generate
HTTP_SECTIONS = [
    ("hook", "Every click is a conversation", ["LO1"], 0, """You type `example.com/products` into a browser and a page appears. Behind that page is a short, structured conversation: your browser sends an **HTTP request** and a server sends back an **HTTP response** [S3].

In this lesson you will learn the two request methods you will use most — **GET** and **POST** — and how to read the server's reply from its status code alone.

> **Think first:** when a form submission fails, how could you tell whether *you* sent something wrong or the *server* broke?"""),
    ("concept", "Anatomy of a request and a response", ["LO1", "LO2", "LO3"], 0, """A request starts with a **method**, a **target** and a protocol version, followed by header fields and optional content (the body) [S3]. A response starts with a **status code** and reason phrase, followed by headers and optional content [S3].

```http
GET /products?category=shoes HTTP/1.1
Host: shop.example.com
Accept: application/json
```

```http
HTTP/1.1 200 OK
Content-Type: application/json

[{"id": 7, "name": "Trail runner"}]
```"""),
    ("concept", "GET — ask for a representation", ["LO1"], 0, """**GET** requests a representation of the specified resource and should only retrieve data [S1]. Two properties make GET special:

- **Safe** — it should not change state on the server [S1].
- **Idempotent** — sending the same request several times has the same effect as sending it once [S1].

Parameters usually travel in the URL **query string**, e.g. `/search?q=shoes` [S1]. Content inside a GET request has no generally defined meaning, and some servers reject it, so don't put a body on GET [S3]. Because GET is safe, its responses are cacheable by default [S1].

**Use GET for:** loading a page, searching, fetching a list of products."""),
    ("concept", "POST — send data to be processed", ["LO2"], 0, """**POST** sends data in the **request body** for the target resource to process according to its own rules — for example creating a record or submitting a form [S1][S3]. The format of the body is declared with the `Content-Type` header, such as `application/json` [S1].

POST is **neither safe nor idempotent**: submitting the same order twice can create two orders [S1]. When a POST creates a new resource, the server should answer **201 Created** with a `Location` header pointing to it [S3].

| | GET | POST |
|---|---|---|
| Purpose | Retrieve data | Submit data for processing |
| Data location | URL query string | Request body |
| Safe? | Yes | No |
| Idempotent? | Yes | No |
| Cacheable by default? | Yes | Rarely |

*Misconception check:* GET and POST don't just differ in **where** data goes — they differ in **what the request means** to the server."""),
    ("concept", "Status code classes: who needs to act?", ["LO3"], 0, """A status code is a three-digit number whose **first digit defines its class** [S3]. There are five classes: 1xx informational, 2xx successful, 3xx redirection, 4xx client error and 5xx server error [S2]. This lesson focuses on the three you meet most:

| Class | Meaning | Who must act? | Examples |
|---|---|---|---|
| **2xx** | Request received, understood and accepted [S3] | Nobody — it worked | 200 OK, 201 Created, 204 No Content [S2] |
| **4xx** | The client seems to have erred [S3] | The **client** must fix the request | 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found [S2] |
| **5xx** | The server failed or cannot perform the request [S3] | The **server** side | 500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable [S2] |

If a client receives a code it does not recognise, it treats it like the `x00` code of that class — an unrecognised `499` is handled like `400` [S3]. So the **first digit alone** tells you who should act.

*Misconception check:* a **404** is not a broken server — the server worked fine and is telling you the resource you asked for doesn't exist [S2]."""),
    ("example", "Worked example: one sign-up, three outcomes", ["LO2", "LO3"], 0, """A user submits a sign-up form. The browser sends:

```http
POST /api/users HTTP/1.1
Content-Type: application/json

{"email": "asha@example.com", "password": "s3cret!"}
```

1. **Everything valid →** `201 Created` with `Location: /api/users/42` — a 2xx because the request succeeded and created a resource [S2][S3].
2. **Email missing →** `400 Bad Request` — a 4xx: the client sent malformed data and must fix it [S2].
3. **Database is down →** `503 Service Unavailable` — a 5xx: the request was fine, the server could not handle it; retrying later may work [S2].

Notice how the *same* request produces different classes depending on **where** the problem lies."""),
    ("activity", "Hands-on: watch real traffic in DevTools", ["LO1", "LO2", "LO3"], 12, """1. Open any website, press **F12** and choose the **Network** tab. Reload the page.
2. Click three requests. For each, note the **method** and **status code**.
3. Find a search box on a site and search for something. Confirm the request is a **GET** and that your search term appears in the URL query string.
4. Submit a login or contact form (with fake data). Find the **POST** request and open its **Payload/Request** tab to see the body.
5. Visit a made-up URL on the same site (e.g. `/this-does-not-exist`) and record the status.

**Expected outcome:** a 3-row table (method, status, class, who must act) including at least one GET, one POST and one 4xx."""),
    ("activity", "Classify the scenario", ["LO3"], 8, """For each scenario, write the **status class** (2xx / 4xx / 5xx) and **who must act**:

1. The API returns the list of products you requested.
2. You call an endpoint without logging in and are told to authenticate.
3. The server crashes with an unhandled exception while saving your order.
4. You request `/users/999` and no such user exists.
5. A gateway receives an invalid response from the upstream server.

**Expected outcome:** 2xx/nobody · 4xx/client (401) · 5xx/server (500) · 4xx/client (404) · 5xx/server (502). Discuss any you got wrong with a partner."""),
    ("summary", "Summary", ["LO1", "LO2", "LO3"], 0, """- **GET** retrieves data, is safe and idempotent, and sends parameters in the URL [S1].
- **POST** submits data in the body for processing, may change server state, and is not idempotent [S1].
- The **first digit** of a status code gives its class: **2xx** success, **4xx** the client must fix the request, **5xx** the server failed [S3]."""),
]

HTTP_QUIZ = [
    ("Which HTTP method should a search page use to fetch results for `?q=shoes`?", ["POST", "GET", "DELETE", "PATCH"], 1,
     "GET retrieves a representation and carries parameters in the query string [S1].", "LO1"),
    ("What does it mean that GET is idempotent?", ["It never returns an error",
     "Repeating the same request has the same effect as sending it once", "It is always encrypted",
     "It can only be sent once per session"], 1, "Idempotent: the same request repeated has the same effect on the server [S1].", "LO1"),
    ("A client submits an order form twice by double-clicking. Why might two orders be created?",
     ["Because POST is not idempotent", "Because GET caches the response", "Because the server returned 404", "Because 2xx means retry"],
     0, "POST is neither safe nor idempotent, so repeating it can repeat its side effects [S1].", "LO2"),
    ("After a POST successfully creates a new user, which response is most appropriate?",
     ["200 OK with no headers", "201 Created with a Location header", "204 No Content", "302 Found"], 1,
     "When POST creates a resource the server should send 201 Created with a Location header [S3].", "LO2"),
    ("You receive `404 Not Found`. Who most likely needs to change something?",
     ["The server administrator, because the server crashed", "The client, because it requested a resource that does not exist",
     "Nobody, because 404 is a success", "The DNS provider"], 1,
     "4xx means the client erred; 404 means the server cannot find the requested resource [S2][S3].", "LO3"),
    ("Which response indicates the request was valid but the server failed to handle it?",
     ["400 Bad Request", "403 Forbidden", "503 Service Unavailable", "201 Created"], 2,
     "5xx codes mean the server erred or cannot perform the request; 503 means it is not ready, e.g. overloaded [S2][S3].", "LO3"),
]


def _generate(ctx):
    research = ctx.get("research", {})
    if _is_http(ctx) and any("mozilla" in s.get("url", "") for s in research.get("sources", [])):
        sections = [{"id": f"sec-{i + 1}", "type": t, "heading": h, "objectives": o, "activity_minutes": a, "content_md": c}
                    for i, (t, h, o, a, c) in enumerate(HTTP_SECTIONS)]
        quiz = [{"id": f"Q{i + 1}", "question": q, "options": o, "answer_index": a, "explanation": e, "objective": lo}
                for i, (q, o, a, e, lo) in enumerate(HTTP_QUIZ)]
    else:
        plan = ctx.get("plan", {})
        src = next((s["id"] for s in research.get("sources", []) if s["type"] == "web"), "S1")
        los = plan.get("sub_objectives", [])
        sections = [{"id": "sec-1", "type": "hook", "heading": "Why this matters", "objectives": [los[0]["id"]] if los else [],
                     "activity_minutes": 0, "content_md": f"This lesson introduces **{ctx.get('title')}** [{src}]."}]
        for i, lo in enumerate(los):
            sections.append({"id": f"sec-{i + 2}", "type": "concept", "heading": lo["text"], "objectives": [lo["id"]],
                             "activity_minutes": 0, "content_md": f"Placeholder explanation for: {lo['text']} [{src}]. " * 8})
        sections.append({"id": "sec-ex", "type": "example", "heading": "Worked example", "objectives": [l["id"] for l in los],
                         "activity_minutes": 0, "content_md": "A worked example would go here (mock mode)."})
        sections.append({"id": "sec-act", "type": "activity", "heading": "Practice", "objectives": [l["id"] for l in los],
                         "activity_minutes": 15, "content_md": "1. Try it yourself.\n2. Compare with a partner."})
        sections.append({"id": "sec-sum", "type": "summary", "heading": "Summary", "objectives": [],
                         "activity_minutes": 0, "content_md": "Key points recap (mock mode)."})
        quiz = [{"id": f"Q{i + 1}", "question": f"Which statement about '{lo['text']}' is correct?",
                 "options": ["Correct statement", "Distractor A", "Distractor B", "Distractor C"], "answer_index": i % 4,
                 "explanation": f"See section on {lo['text']} [{src}].", "objective": lo["id"]} for i, lo in enumerate(los * 2)][:6]
    video = next((s["id"] for s in research.get("sources", []) if s["type"] == "video"), None)
    return {
        "title": ctx.get("title"), "objective": ctx.get("objective"),
        "audience": ctx.get("plan", {}).get("audience", ""), "prerequisites": ctx.get("plan", {}).get("prerequisites", []),
        "sections": sections, "quiz": quiz,
        "videos": [{"source_id": video, "placement": "optional", "why": "A visual walkthrough of the same ideas."}] if video else [],
        "instructor_notes": "Pause after the status-class table and ask learners to predict the class for codes you call out.",
    }


def _fit_budget(ctx):
    return copy.deepcopy(ctx["lesson"])


# ------------------------------------------------------------------ QC layer 1
def _extract_claims(ctx):
    claims = []
    for sec in ctx["lesson"]["sections"]:
        prose = re.sub(r"```.*?```", " ", sec["content_md"], flags=re.S)
        for sent in re.split(r"(?<=[.!?\]])\s+(?=[A-Z*])", prose):
            if re.search(r"\[S\d+\]", sent) and len(sent) > 40 and "|" not in sent:
                claims.append({"id": f"C{len(claims) + 1}", "section_id": sec["id"], "text": re.sub(r"\[S\d+\]", "", sent).strip(),
                               "quote": sent.strip() if sent.strip() in sec["content_md"] else "", "importance": "high" if sec["type"] == "concept" else "medium"})
    return {"claims": claims[:12]}


def _verify_claims(ctx):
    out = []
    for c in ctx["claims"]:
        ev = ctx["evidence"].get(c["id"], [])
        ct = set(tokenize(c["text"]))
        best, best_id = 0.0, []
        for p in ev:
            ov = len(ct & set(tokenize(p["text"]))) / (len(ct) or 1)
            if ov > best:
                best, best_id = ov, [p["id"]]
        verdict = "supported" if best >= 0.5 else ("partially_supported" if best >= 0.3 else "unsupported")
        out.append({"id": c["id"], "verdict": verdict, "evidence_ids": best_id, "knowledge_check": "correct",
                    "explanation": f"Mock check: {round(best * 100)}% term overlap with best passage.", "correction": ""})
    return {"results": out}


def _consistency(ctx):
    lesson = ctx["lesson"]
    target = next((s["id"] for s in lesson["sections"] if s["type"] == "concept"), lesson["sections"][0]["id"])
    return {"issues": [{
        "category": "factual_gap", "severity": "minor", "section_id": target,
        "description": "The lesson does not mention that 3xx redirection responses exist between 2xx and 4xx, which learners will meet in DevTools.",
        "suggestion": "Add one sentence noting 3xx codes (e.g. 301, 304) are redirections, outside this lesson's focus.",
        "fix": {"op": "rewrite_section", "instruction": "Add one sentence noting that 3xx codes are redirection responses, outside this lesson's focus."}}]}


# ------------------------------------------------------------------ QC layer 2
def _instruction_review(ctx):
    lesson = ctx["lesson"]
    act = next((s["id"] for s in lesson["sections"] if s["type"] == "activity"), lesson["sections"][-1]["id"])
    crit = ["objective_alignment", "clarity", "structure", "examples", "practical_application", "assessment", "engagement", "pacing"]
    return {
        "scores": {k: {"score": 4, "justification": f"Mock rubric score for {k.replace('_', ' ')}."} for k in crit},
        "strengths": ["Each sub-objective has a matching quiz item", "Worked example contrasts three outcomes of one request"],
        "issues": [{"category": "practice", "severity": "minor", "section_id": act,
                    "description": "The activity does not tell learners what to do if a site shows no POST requests.",
                    "suggestion": "Suggest a fallback site such as httpbin.org/forms/post.",
                    "fix": {"op": "rewrite_section", "instruction": "Add a fallback: if no form is available, use https://httpbin.org/forms/post."}}],
    }


# ------------------------------------------------------------------ apply fixes
def _rewrite_section(ctx):
    sec = copy.deepcopy(ctx["section"])
    sec["content_md"] = sec["content_md"].rstrip() + f"\n\n*Revision:* {ctx['instruction']}"
    return sec


def _add_section(ctx):
    return {"id": "sec-new", "type": "concept", "heading": "Additional explanation", "objectives": [], "activity_minutes": 0,
            "content_md": f"(Mock) New section addressing: {ctx['instruction']}"}


def _rewrite_quiz(ctx):
    return {"quiz": copy.deepcopy(ctx["quiz"])}


def _outline(ctx):
    full = _generate(ctx)
    for sec in full["sections"]:
        sec["brief"] = sec["heading"]
        sec["_content"] = sec.pop("content_md")
    _outline.cache = {sec["id"]: sec.pop("_content") for sec in full["sections"]}
    return full


def _sections(ctx):
    cache = getattr(_outline, "cache", {})
    return {"sections": [{"id": b["id"], "content_md": cache.get(b["id"], "Mock content [S1].")} for b in ctx["batch"]]}
