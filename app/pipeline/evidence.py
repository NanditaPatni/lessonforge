"""Evidence retrieval over the research passages (BM25, credibility-weighted)."""
from __future__ import annotations

from ..research.text import BM25, tokenize


class EvidenceIndex:
    def __init__(self, research: dict, extra_passages: list[dict] | None = None):
        self.passages = list(research.get("passages", [])) + list(extra_passages or [])
        self.sources = {s["id"]: s for s in research.get("sources", [])}
        self.bm = BM25([tokenize(p["text"]) for p in self.passages])

    def cred(self, source_id: str) -> float:
        return (self.sources.get(source_id, {}).get("credibility") or {}).get("score", 0.5)

    def search(self, query: str, k: int = 4) -> list[dict]:
        hits = self.bm.top(tokenize(query), k * 3)
        ranked = sorted(((s * (0.6 + 0.4 * self.cred(self.passages[i]["source_id"])), i) for s, i in hits), reverse=True)
        return [self.passages[i] for _, i in ranked[:k]]

    def pack(self, queries: list[str], per_query: int = 4, cap: int = 26) -> list[dict]:
        seen, out = set(), []
        for q in queries:
            for p in self.search(q, per_query):
                if p["id"] not in seen:
                    seen.add(p["id"])
                    out.append(p)
        # Guarantee every source contributes at least its first passage (diversity).
        for sid in self.sources:
            if not any(p["source_id"] == sid for p in out):
                first = next((p for p in self.passages if p["source_id"] == sid), None)
                if first:
                    out.append(first)
        return out[:cap]


def fmt_passages(passages: list[dict], max_words: int = 120) -> str:
    lines = []
    for p in passages:
        words = p["text"].split()
        text = " ".join(words[:max_words]) + (" …" if len(words) > max_words else "")
        lines.append(f"[{p['id']}] {text}")
    return "\n".join(lines) or "(no evidence)"


def fmt_sources(sources: list[dict]) -> str:
    out = []
    for s in sources:
        kind = s["type"]
        meta = s.get("domain") or s.get("publisher") or s.get("channel") or ""
        if kind == "video" and s.get("duration_sec"):
            meta += f", {round(s['duration_sec'] / 60, 1)} min"
        out.append(f"{s['id']} ({kind}, {s['credibility']['tier']}) {s['title']} — {meta}")
    return "\n".join(out)
