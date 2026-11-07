"""QC Layer 1 — Research & factual accuracy.

  A. Citation integrity (deterministic): uncited factual sections, over-reliance on one source,
     low-credibility sources, dead reference links.
  B. Claim-level verification: an independent model extracts atomic claims, each claim gets its
     own BM25-retrieved evidence, and is judged on TWO signals — grounded in evidence? correct per
     expert knowledge? — so "true but unsourced" is distinguished from "wrong".
  C. Second-opinion retrieval: claims that are unsupported or contradicted trigger a fresh web
     search that did NOT come from the generator's research, and are re-verified against it.
  D. Holistic audit: internal inconsistencies, misleading simplifications, wrong quiz keys, gaps.
"""
from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .. import prompts
from ..config import get_settings
from ..llm import LLMClient
from ..research.credibility import score_web
from ..research.text import BM25, chunk_text, tokenize
from .evidence import EvidenceIndex, fmt_passages
from .issues import as_list, as_object, finalize, llm_issue, make_issue
from .lesson_model import all_sources, citations_in, lesson_as_text

MAX_CLAIMS = 25
MAX_SECOND_OPINION = 4
SECOND_OPINION_BUDGET = 150   # seconds for the whole independent re-search; never stall the QC step


def _validate_claims(d):
    if isinstance(d, list):
        raise ValueError("expected an object {\"claims\": [...]}, got a bare list")
    if not isinstance(d.get("claims"), list):
        raise ValueError("'claims' list missing")


def _validate_results(d):
    if isinstance(d, list):
        raise ValueError("expected an object {\"results\": [...]}, got a bare list")
    if not isinstance(d.get("results"), list):
        raise ValueError("'results' list missing")


def _verify(llm: LLMClient, claims: list[dict], evidence_by_claim: dict[str, list[dict]]) -> dict[str, dict]:
    block = []
    for c in claims:
        block.append(f"### {c['id']}: {c['text']}\nEvidence:\n{fmt_passages(evidence_by_claim[c['id']], 110)}")
    data = llm.json(prompts.VERIFY_SYSTEM, prompts.VERIFY_USER.format(claims="\n\n".join(block)), task="verify_claims",
                    context={"claims": claims, "evidence": evidence_by_claim}, validate=_validate_results, temperature=0.0)
    return {r.get("id"): r for r in data["results"] if isinstance(r, dict)}


def _second_opinion(claim: dict, llm: LLMClient, next_id: int) -> dict | None:
    """Independent search for one claim. Returns {source, passage, result} or None."""
    from ..research.web import fetch_page, search_web
    try:
        hits = search_web(claim["text"], n=6)
    except Exception:
        return None
    # Never "confirm" a claim with a homework-answer / user-generated site.
    hits = [h for h in hits if score_web(h["url"])["tier"] != "low"]
    hits = sorted(hits, key=lambda h: score_web(h["url"])["score"], reverse=True)[:3]
    for h in hits:
        try:
            text = h.get("content") or fetch_page(h["url"], timeout=12)
        except Exception:
            continue
        chunks = chunk_text(text)
        if not chunks:
            continue
        bm = BM25([tokenize(c) for c in chunks])
        top = bm.top(tokenize(claim["text"]), 2)
        if not top:
            continue
        sid = f"S{next_id}"
        passages = [{"id": f"{sid}.p{i + 1}", "source_id": sid, "text": chunks[j]} for i, (_, j) in enumerate(top)]
        res = _verify(llm, [claim], {claim["id"]: passages}).get(claim["id"])
        if res and res.get("verdict") in ("supported", "contradicted"):
            return {
                "source": {"id": sid, "type": "web", "title": h.get("title", "")[:200], "url": h["url"],
                           "domain": h["url"].split("/")[2] if "//" in h["url"] else "", "snippet": passages[0]["text"][:240],
                           "credibility": score_web(h["url"]), "relevance": 0, "added_by": "qc_research"},
                "passages": passages, "result": res,
            }
    return None


def run(lesson: dict, research: dict, llm: LLMClient, version: int, emit: Callable[[str], None] = lambda m: None) -> dict:
    s = get_settings()
    sources = all_sources(research, lesson)
    by_id = {x["id"]: x for x in sources}
    sections = {x["id"]: x for x in lesson["sections"]}
    idx = EvidenceIndex(research)
    issues: list[dict] = []
    warnings: list[str] = []   # parts of the audit that could not run; the report is still produced

    def soft(part: str, err: Exception) -> None:
        msg = f"{part} skipped: {str(err)[:200]}"
        warnings.append(msg)
        emit("⚠ " + msg)

    # ------------------------------------------------ A. citation integrity
    cite_counter: Counter = Counter()
    for sec in lesson["sections"]:
        cite_counter.update(citations_in(sec["content_md"]))
        if sec["type"] in ("concept", "example") and not citations_in(sec["content_md"]):
            issues.append(make_issue(
                "citation", "major", sec["id"], f"Section '{sec['heading']}' makes factual statements without any citation.",
                "Attach [S#] citations from the research evidence to each factual statement; remove any statement the evidence does not support.",
                {"op": "rewrite_section", "instruction": "Add [S#] citations from the evidence to every factual statement. Remove or soften statements the evidence does not support."}))
    for q in lesson.get("quiz", []):
        cite_counter.update(citations_in(q.get("explanation", "")))
    total_cites = sum(cite_counter.values())
    if total_cites >= 6:
        top_src, n = cite_counter.most_common(1)[0]
        if n / total_cites > 0.6:
            issues.append(make_issue(
                "source_diversity", "minor", "", f"{round(100 * n / total_cites)}% of citations point to a single source ({top_src}).",
                "Corroborate key claims with a second independent source to reduce single-source risk.", {"op": "manual"}))
    for sid in cite_counter:
        src = by_id.get(sid)
        if src and src["credibility"]["tier"] == "low":
            issues.append(make_issue("source_reliability", "major", "",
                                     f"Lesson cites a low-credibility source {sid} ({src.get('domain', '')}): {src['credibility']['reason']}.",
                                     "Replace these citations with an authoritative source or remove the dependent claims.", {"op": "manual"}))

    # ------------------------------------------------ link health
    link_status: dict[str, dict] = {}
    if s.check_links and s.research_mode != "mock":
        try:
            from ..research.web import check_link
            targets = [x for x in sources if x.get("url") and (x["id"] in cite_counter or x["type"] != "web")]
            with ThreadPoolExecutor(max_workers=8) as ex:
                for src, st in zip(targets, ex.map(lambda x: check_link(x["url"]), targets)):
                    link_status[src["id"]] = st
                    if not st["ok"]:
                        issues.append(make_issue("dead_link", "minor", "", f"Reference {src['id']} ({src['url']}) is unreachable (status {st.get('status') or st.get('error')}).",
                                                 "Replace the link or remove the reference.", {"op": "manual"}))
            emit(f"Checked {len(targets)} reference links: {sum(1 for v in link_status.values() if not v['ok'])} unreachable")
        except Exception as e:  # noqa: BLE001
            soft("Link check", e)

    # ------------------------------------------------ B. claim verification
    text = lesson_as_text(lesson)
    try:
        data = llm.json(prompts.CLAIMS_SYSTEM, prompts.CLAIMS_USER.format(max_claims=MAX_CLAIMS, lesson=text),
                        task="extract_claims", context={"lesson": lesson}, validate=_validate_claims, temperature=0.0)
    except Exception as e:  # noqa: BLE001
        soft("Claim extraction", e)
        data = {"claims": []}
    claims = []
    for i, c in enumerate(data["claims"][:MAX_CLAIMS]):
        if isinstance(c, dict) and c.get("text"):
            claims.append({"id": f"C{i + 1}", "section_id": str(c.get("section_id") or ""), "text": str(c["text"]),
                           "quote": str(c.get("quote") or ""), "importance": c.get("importance", "medium")})
    # Quiz answer-key claims like "The correct answer to Q2 is B" are unverifiable as written:
    # expand them with the real question and option text before fact-checking.
    quiz_by_id = {q["id"]: q for q in lesson.get("quiz", [])}
    for c in claims:
        m = re.search(r"\bQ(\d+)\b", c["text"])
        if (c["section_id"] == "quiz" or m) and m and f"Q{m.group(1)}" in quiz_by_id:
            q = quiz_by_id[f"Q{m.group(1)}"]
            c["section_id"] = "quiz"
            c["text"] = f"For the question \"{q['question']}\", the correct answer is \"{q['options'][q['answer_index']]}\""
    emit(f"Extracted {len(claims)} factual claims")
    ev = {c["id"]: idx.search(c["text"] + " " + c["quote"], 3) for c in claims}
    results: dict[str, dict] = {}
    for i in range(0, len(claims), 8):
        try:
            results.update(_verify(llm, claims[i:i + 8], ev))
        except Exception as e:  # noqa: BLE001
            soft(f"Verification of claims {i + 1}-{min(i + 8, len(claims))}", e)
    for c in claims:
        r = results.get(c["id"], {})
        # A claim the reviewer did not return a verdict for is "unchecked" (not counted), never silently "unsupported".
        c["verdict"] = r.get("verdict") if r.get("verdict") in ("supported", "partially_supported", "unsupported", "contradicted") else "unchecked"
        c["knowledge_check"] = r.get("knowledge_check") if r.get("knowledge_check") in ("correct", "incorrect", "uncertain") else "uncertain"
        c["explanation"] = r.get("explanation", "")
        c["correction"] = r.get("correction", "") or ""
        c["evidence_ids"] = [e for e in (r.get("evidence_ids") or []) if isinstance(e, str)]
        c["evidence"] = [{"id": p["id"], "text": p["text"][:400]} for p in ev[c["id"]]]
        c["second_opinion"] = None
    emit("Verified claims against evidence: " + ", ".join(f"{k}={v}" for k, v in Counter(c["verdict"] for c in claims).items()))

    # ------------------------------------------------ C. second-opinion retrieval
    new_sources: list[dict] = []
    if s.research_mode != "mock":
        doubtful = [c for c in claims if c["verdict"] in ("unsupported", "contradicted")]
        doubtful.sort(key=lambda c: {"high": 0, "medium": 1, "low": 2}.get(c["importance"], 1))
        next_id = max((int(x["id"][1:]) for x in sources if x["id"][1:].isdigit()), default=0) + 1
        import time as _time
        from ..research.web import with_deadline
        started = _time.time()
        for c in doubtful[:MAX_SECOND_OPINION]:
            remaining = SECOND_OPINION_BUDGET - (_time.time() - started)
            if remaining < 15:
                soft("Independent re-search (time budget reached)", TimeoutError(f"stopped after {SECOND_OPINION_BUDGET}s"))
                break
            try:
                so = with_deadline(_second_opinion, remaining, c, llm, next_id)
            except Exception as e:  # noqa: BLE001
                soft(f"Independent re-search for {c['id']}", e)
                so = None
            if so:
                c["second_opinion"] = {"source_id": so["source"]["id"], "url": so["source"]["url"],
                                       "verdict": so["result"]["verdict"], "explanation": so["result"].get("explanation", "")}
                so["source"]["passages"] = so["passages"]
                new_sources.append(so["source"])
                next_id += 1
        if doubtful:
            emit(f"Independent re-search on {min(len(doubtful), MAX_SECOND_OPINION)} doubtful claims, {len(new_sources)} resolved with new sources")

    for c in claims:
        sec = sections.get(c["section_id"])
        located = bool(sec and c["quote"] and c["quote"] in sec["content_md"])
        so = c["second_opinion"]
        wrong = c["verdict"] == "contradicted" or c["knowledge_check"] == "incorrect"
        if so and so["verdict"] == "contradicted":
            wrong = True
        if wrong:
            if c["section_id"] == "quiz":
                fix = {"op": "rewrite_quiz", "instruction": f"Fix the quiz item: {c['text']} — {c['explanation']} {c['correction']}"}
            elif located and c["correction"]:
                fix = {"op": "replace_text", "find": c["quote"], "replace": c["correction"]}
            else:
                fix = {"op": "rewrite_section", "instruction": f"Correct this claim: '{c['text']}'. {c['explanation']} {c['correction']}"}
            issues.append(make_issue("factual_error", "critical", c["section_id"], f"Incorrect claim ({c['id']}): {c['text']} — {c['explanation']}",
                                     c["correction"] or "Correct or remove this claim.", fix, source="claim_check", claim_id=c["id"]))
        elif c["verdict"] == "unsupported" and so and so["verdict"] == "supported":
            src = next(x for x in new_sources if x["id"] == so["source_id"])
            fix = {"op": "add_citation", "find": c["quote"] if located else "", "source": src, "instruction": f"Cite {src['id']} for: {c['text']}"}
            issues.append(make_issue("unsupported_claim", "minor", c["section_id"],
                                     f"Claim {c['id']} was not backed by the original research but was confirmed by an independent source ({src['domain']}).",
                                     f"Add citation {src['id']} ({src['url']}).", fix, source="second_opinion", claim_id=c["id"]))
        elif c["verdict"] == "unsupported":
            sev = "minor" if c["knowledge_check"] == "correct" or c["section_id"] == "quiz" else "major"
            issues.append(make_issue("unsupported_claim", sev, c["section_id"],
                                     f"Claim {c['id']} is not supported by the cited research: {c['text']}" +
                                     (" (reviewer believes it is correct but it needs a source)" if sev == "minor" else ""),
                                     "Support it with a reliable source, qualify it, or remove it.",
                                     {"op": "rewrite_quiz", "instruction": f"Check this answer key against the evidence and fix it if needed; add a [S#] citation to its explanation: {c['text']}"}
                                     if c["section_id"] == "quiz" else
                                     {"op": "rewrite_section", "instruction": f"The claim '{c['text']}' is not supported by the evidence. Rephrase it so it only states what the evidence supports, with citations, or remove it."},
                                     source="claim_check", claim_id=c["id"]))
        elif c["verdict"] == "partially_supported" and c["correction"]:
            fix = {"op": "replace_text", "find": c["quote"], "replace": c["correction"]} if located else \
                  {"op": "rewrite_section", "instruction": f"Make this claim precise: '{c['text']}'. {c['correction']}"}
            issues.append(make_issue("imprecise_claim", "major" if c["importance"] == "high" else "minor", c["section_id"],
                                     f"Claim {c['id']} is only partially supported / imprecise: {c['explanation']}",
                                     c["correction"], fix, source="claim_check", claim_id=c["id"]))

    # ------------------------------------------------ D. holistic audit
    audit_ev = idx.pack([lesson["objective"]] + [lo["text"] for lo in lesson.get("sub_objectives", [])], per_query=3, cap=8 if s.compact else 12)
    try:
        audit = llm.json(prompts.CONSISTENCY_SYSTEM,
                         prompts.CONSISTENCY_USER.format(objective=lesson["objective"], evidence=fmt_passages(audit_ev, 90), lesson=text),
                         task="consistency", context={"lesson": lesson}, temperature=0.1)
    except Exception as e:  # noqa: BLE001
        soft("Consistency audit", e)
        audit = {"issues": []}
    audit = as_object(audit, "issues")
    for raw in as_list(audit.get("issues"))[:12]:
        it = llm_issue(raw, set(sections), source="consistency_audit")
        if it:
            issues.append(it)

    issues = finalize(issues, "R")
    cnt = Counter(c["verdict"] for c in claims)
    n = max(1, len(claims) - cnt["unchecked"])
    errors = sum(1 for c in claims if c["verdict"] == "contradicted" or c["knowledge_check"] == "incorrect")
    grounded = round(100 * (cnt["supported"] + 0.5 * cnt["partially_supported"]) / n)
    crit = sum(1 for i in issues if i["severity"] == "critical")
    status = "fail" if crit else ("pass" if grounded >= 70 and not any(i["severity"] == "major" for i in issues) else "needs_review")
    tiers = Counter(x["credibility"]["tier"] for x in sources)
    return {
        "layer": "research",
        "version": version,
        "model": llm.label,
        "status": status,
        "metrics": {
            "claims_checked": len(claims), "supported": cnt["supported"], "partially_supported": cnt["partially_supported"],
            "unsupported": cnt["unsupported"], "contradicted": cnt["contradicted"], "unchecked": cnt["unchecked"],
            "factual_errors": errors,
            "grounding_score": grounded, "sources_total": len(sources), "sources_cited": len(cite_counter),
            "authoritative_sources": tiers["authoritative"], "low_credibility_sources": tiers["low"],
            "dead_links": sum(1 for v in link_status.values() if not v["ok"]), "independently_verified": len(new_sources),
        },
        "claims": claims,
        "sources": [{"id": x["id"], "title": x["title"], "type": x["type"], "url": x["url"], "tier": x["credibility"]["tier"],
                     "score": x["credibility"]["score"], "reason": x["credibility"]["reason"], "times_cited": cite_counter.get(x["id"], 0),
                     "link": link_status.get(x["id"])} for x in sources],
        "new_sources": new_sources,
        "issues": issues,
        "warnings": warnings,
    }
