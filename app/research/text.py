"""Text utilities: tokenising, chunking, a small BM25 retriever and readability metrics.

BM25 is implemented here (≈30 lines) rather than pulling in a vector DB: the corpus per
lesson is a few hundred passages, lexical retrieval is transparent/explainable, and it
works offline with zero extra setup.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_STOP = frozenset(
    """a an the and or but of to in for on with is are was were be been being by as at it its this that
    these those from how what why when which who can could will would should may might your you we our their
    they them into about than then not no do does did has have had if so such also more most other some any
    each all just only over very use used using one two between within e.g i.e""".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and (len(t) > 1 or t.isdigit())]


def strip_markdown(md: str) -> str:
    md = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    md = re.sub(r"`[^`]*`", " ", md)
    md = re.sub(r"\[S\d+(?:\.p\d+)?(?:\s*[,;]\s*S\d+(?:\.p\d+)?)*\]", " ", md)
    md = re.sub(r"[#>*_|\-]+", " ", md)
    return re.sub(r"\s+", " ", md).strip()


def word_count(md: str) -> int:
    return len(strip_markdown(md).split())


def code_lines(md: str) -> int:
    return sum(len(b.strip().splitlines()) for b in re.findall(r"```.*?\n(.*?)```", md or "", flags=re.S))


def chunk_text(text: str, size: int = 130, overlap: int = 30, max_chunks: int = 40) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    out, step = [], size - overlap
    for i in range(0, len(words), step):
        out.append(" ".join(words[i:i + size]))
        if i + size >= len(words) or len(out) >= max_chunks:
            break
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.N = len(docs)
        self.avgdl = (sum(map(len, docs)) / self.N) if self.N else 0.0
        self.tf = [Counter(d) for d in docs]
        self.df: Counter = Counter()
        for d in docs:
            self.df.update(set(d))

    def idf(self, t: str) -> float:
        n = self.df.get(t, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, q: list[str], i: int) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in set(q):
            f = tf.get(t, 0)
            if f:
                s += self.idf(t) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
        return s

    def top(self, q: list[str], k: int = 5) -> list[tuple[float, int]]:
        scored = [(self.score(q, i), i) for i in range(self.N)]
        return [x for x in sorted(scored, reverse=True)[:k] if x[0] > 0]


def term_coverage(terms: list[str], text: str) -> float:
    """Fraction of query terms present in text (0..1) — cheap relevance signal."""
    q = set(terms)
    if not q:
        return 0.0
    return len(q & set(tokenize(text))) / len(q)


def _syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups)
    if word.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def flesch_reading_ease(md: str) -> float:
    text = strip_markdown(md)
    sentences = max(1, len(re.findall(r"[.!?]+(\s|$)", text)))
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 100.0
    syl = sum(_syllables(w) for w in words)
    return round(206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syl / len(words)), 1)
