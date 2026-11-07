"""Transparent, rule-based source credibility scoring.

Deliberately NOT an LLM judgement: instructors can read exactly why a source got
its tier, and the score is deterministic across runs.
"""
from __future__ import annotations

from urllib.parse import urlparse

AUTHORITATIVE = {
    "developer.mozilla.org": "MDN Web Docs — maintained reference documentation",
    "rfc-editor.org": "RFC Editor — official Internet standards",
    "datatracker.ietf.org": "IETF — official Internet standards",
    "ietf.org": "IETF — official Internet standards",
    "httpwg.org": "IETF HTTP Working Group",
    "w3.org": "W3C — web standards body",
    "whatwg.org": "WHATWG — web standards body",
    "iana.org": "IANA — official protocol registries",
    "docs.python.org": "Official Python documentation",
    "learn.microsoft.com": "Official Microsoft documentation",
    "developers.google.com": "Official Google developer documentation",
    "cloud.google.com": "Official Google Cloud documentation",
    "docs.aws.amazon.com": "Official AWS documentation",
    "web.dev": "Google web.dev guidance",
    "postgresql.org": "Official PostgreSQL documentation",
    "dev.mysql.com": "Official MySQL documentation",
    "sqlite.org": "Official SQLite documentation",
    "nodejs.org": "Official Node.js documentation",
    "react.dev": "Official React documentation",
    "kubernetes.io": "Official Kubernetes documentation",
    "git-scm.com": "Official Git documentation",
    "docs.github.com": "Official GitHub documentation",
    "docs.oracle.com": "Official Oracle documentation",
    "en.cppreference.com": "cppreference — community-maintained standard reference",
    "nist.gov": "NIST — US standards agency",
}

REPUTABLE = {
    "en.wikipedia.org": "Wikipedia — broad, community-edited; verify key facts",
    "freecodecamp.org": "freeCodeCamp — established educational nonprofit",
    "cloudflare.com": "Cloudflare Learning Center — vendor education content",
    "digitalocean.com": "DigitalOcean tutorials — editorially reviewed",
    "realpython.com": "Real Python — editorially reviewed tutorials",
    "ibm.com": "IBM — vendor education content",
    "aws.amazon.com": "AWS — vendor education content",
    "khanacademy.org": "Khan Academy — educational nonprofit",
    "martinfowler.com": "Martin Fowler — recognised practitioner",
    "baeldung.com": "Baeldung — editorially reviewed tutorials",
    "geeksforgeeks.org": "GeeksforGeeks — popular, variable quality",
    "w3schools.com": "W3Schools — popular, simplified tutorials",
    "tutorialspoint.com": "Tutorialspoint — popular, variable quality",
    "stackoverflow.com": "Stack Overflow — community Q&A, check accepted answers",
    "oreilly.com": "O'Reilly — technical publisher",
    "postman.com": "Postman — API tooling vendor education",
    "restfulapi.net": "REST API tutorial site",
}

LOW_HINTS = ("medium.com", "quora.com", "reddit.com", "blogspot.", "wordpress.com", "pinterest.",
             "facebook.com", "linkedin.com", "scribd.com", "slideshare.net", "brainly.", "chegg.com",
             # homework-answer / user-uploaded content sites
             "askfilo.com", "coursehero.com", "studocu.com", "quizlet.com", "sarthaks.com", "toppr.com",
             "numerade.com", "bartleby.com", "doubtnut.com", "answers.com", "transtutors.com")

GOOD_PUBLISHERS = ("o'reilly", "oreilly", "manning", "pearson", "addison", "no starch", "apress", "wiley",
                   "mit press", "springer", "cambridge", "oxford", "prentice", "mcgraw", "pragmatic",
                   "packt", "morgan kaufmann", "elsevier", "sams", "microsoft press")

KNOWN_CHANNELS = ("freecodecamp", "cs50", "computerphile", "fireship", "ibm technology", "hussein nasser",
                  "traversy", "web dev simplified", "google for developers", "microsoft developer",
                  "neso academy", "mit opencourseware", "khan academy", "crashcourse", "aws", "programming with mosh",
                  "net ninja", "bytebytego", "techworld with nana", "corey schafer", "codecademy", "mdn")


def _host(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _match(host: str, table: dict) -> str | None:
    for dom, why in table.items():
        if host == dom or host.endswith("." + dom):
            return why
    return None


def score_web(url: str) -> dict:
    host = _host(url)
    why = _match(host, AUTHORITATIVE)
    if why:
        return {"score": 0.95, "tier": "authoritative", "reason": why}
    if host.endswith((".gov", ".edu")) or ".ac." in host or ".gov." in host or ".edu." in host:
        return {"score": 0.85, "tier": "authoritative", "reason": "Government / academic domain"}
    why = _match(host, REPUTABLE)
    if why:
        return {"score": 0.72, "tier": "reputable", "reason": why}
    if any(h in host for h in LOW_HINTS):
        return {"score": 0.3, "tier": "low", "reason": "User-generated / unvetted platform"}
    return {"score": 0.55, "tier": "general", "reason": "Unclassified site — not on curated lists"}


def score_book(publisher: str, year: int | None, current_year: int = 2026) -> dict:
    pub = (publisher or "").lower()
    good = any(p in pub for p in GOOD_PUBLISHERS)
    score = 0.8 if good else 0.6
    reason = f"Published by {publisher}" if publisher else "Publisher unknown"
    if good:
        reason += " (established technical/academic publisher)"
    if year and current_year - year > 15:
        score -= 0.1
        reason += f"; older edition ({year}) — fundamentals only"
    return {"score": round(score, 2), "tier": "reputable" if score >= 0.7 else "general", "reason": reason}


def score_video(channel: str, views: int | None) -> dict:
    ch = (channel or "").lower()
    if any(k in ch for k in KNOWN_CHANNELS):
        return {"score": 0.8, "tier": "reputable", "reason": f"Established educational channel ({channel})"}
    if views and views >= 100_000:
        return {"score": 0.65, "tier": "general", "reason": f"Widely viewed ({views:,} views)"}
    return {"score": 0.5, "tier": "general", "reason": f"Channel not on curated list ({channel or 'unknown'})"}
