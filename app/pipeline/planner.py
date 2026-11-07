"""Step 1 — objective decomposition + research plan."""
from __future__ import annotations

from .. import prompts
from ..config import get_settings
from ..llm import LLMClient


def _validate(d: dict) -> None:
    los = d.get("sub_objectives")
    if not isinstance(los, list) or not (1 <= len(los) <= 6):
        raise ValueError("sub_objectives must be a list of 1-6 items")
    for i, lo in enumerate(los):
        if not isinstance(lo, dict) or not lo.get("text"):
            raise ValueError("each sub_objective needs 'text'")
        lo["id"] = f"LO{i + 1}"
    q = d.get("search_queries")
    if not isinstance(q, dict) or not q.get("web"):
        raise ValueError("search_queries.web is required")


def make_plan(title: str, objective: str, llm: LLMClient) -> dict:
    minutes = get_settings().lesson_minutes
    plan = llm.json(
        prompts.PLAN_SYSTEM,
        prompts.PLAN_USER.format(title=title, objective=objective, minutes=minutes),
        task="plan",
        context={"title": title, "objective": objective},
        validate=_validate,
    )
    plan.setdefault("key_concepts", [])
    plan.setdefault("common_misconceptions", [])
    plan.setdefault("prerequisites", [])
    q = plan["search_queries"]
    q.setdefault("books", [title])
    q.setdefault("videos", [f"{title} explained"])
    plan["title"], plan["objective"] = title, objective
    return plan
