"""Provider-agnostic LLM client.

* One interface (`LLMClient.json`) for Gemini, Groq, OpenAI, OpenRouter, Ollama, Anthropic
  and an offline `mock` provider used by tests.
* Retries with exponential backoff + jitter on rate limits / transient errors.
* Forces JSON output and re-asks the model when the reply is not valid JSON or fails
  a caller-supplied validator — so every downstream step receives structured data.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Callable

import httpx

from .config import get_settings
from .shapes import normalize_reply  # noqa: F401  (re-exported for callers/tests)

log = logging.getLogger("lessonforge.llm")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}

OPENAI_COMPAT_BASE = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def _is_daily_quota(text: str) -> bool:
    t = text.lower()
    return ("perday" in t or "per day" in t or "(tpd)" in t or "(rpd)" in t) and ("quota" in t or "limit" in t)


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or never returns usable output."""


def extract_json(raw: str) -> Any:
    """Parse JSON from a model reply, tolerating a ```json fence around the WHOLE reply and leading prose.

    Important: lesson content itself contains code blocks (e.g. a raw ```http response inside content_md),
    so a fence is only stripped when it wraps the entire reply — never matched inside JSON strings.
    """
    text = (raw or "").strip()
    try:
        return json.loads(text)                      # the common case: the reply is pure JSON
    except json.JSONDecodeError:
        pass
    if text.startswith("```"):                       # the whole reply wrapped in a fence
        inner = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        inner = re.sub(r"\s*```\s*$", "", inner)
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            text = inner
    # prose before/after the JSON: take the outermost object/array
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON object found in model reply")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    if end <= start:
        raise ValueError("no complete JSON object found in model reply (output may be truncated)")
    return json.loads(text[start:end + 1])


class LLMClient:
    def __init__(self, role: str = "generator"):
        s = get_settings()
        self.settings = s
        self.role = role
        if role == "qc":
            self.provider, self.model = s.qc_provider, s.qc_model
        else:
            self.provider, self.model = s.llm_provider, s.llm_model
        self.calls = 0
        self.fallback_used = ""
        self.last_finish = ""

    @property
    def label(self) -> str:
        base = f"{self.provider}:{self.model}"
        if self.fallback_used:
            base += f" (+fallback {self.fallback_used})"
        return base

    def _chain(self):
        return [fb for fb in self.settings.fallbacks if fb != (self.provider, self.model)]

    def _with_fallbacks(self, fn, what: str):
        """Run fn() on the primary model, then on each FALLBACK_LLM entry until one succeeds.
        Sticky: once a fallback has worked, this client keeps using it (no time wasted on a busy model)."""
        sticky = getattr(self, "_sticky", None)
        if sticky:
            orig = (self.provider, self.model)
            self.provider, self.model = sticky
            try:
                return fn(False)
            except LLMError:
                self._sticky = None   # the sticky model failed too; walk the full chain again
            finally:
                self.provider, self.model = orig
        try:
            return fn(True)
        except LLMError as primary_err:
            errors = [f"{self.provider}:{self.model}: {primary_err}"]
            orig = (self.provider, self.model)
            for fb in self._chain():
                log.warning("%s failed — trying fallback %s:%s", what, *fb)
                self.provider, self.model = fb
                try:
                    out = fn(False)
                    self.fallback_used = f"{fb[0]}:{fb[1]}"
                    self._sticky = fb
                    return out
                except LLMError as e:
                    errors.append(f"fallback {fb[0]}:{fb[1]}: {e}")
                finally:
                    self.provider, self.model = orig
            if len(errors) == 1:
                raise
            raise LLMError(" | ".join(errors)) from primary_err

    # ------------------------------------------------------------------ public
    def json(
        self,
        system: str,
        user: str,
        task: str,
        context: dict | None = None,
        validate: Callable[[Any], None] | None = None,
        temperature: float = 0.3,
    ) -> Any:
        """Return parsed JSON. `context` is only used by the offline mock provider."""
        if self.provider == "mock":
            from . import mock_llm
            data = normalize_reply(task, mock_llm.respond(task, context or {}))
            if validate:
                validate(data)
            return data

        return self._with_fallbacks(
            lambda primary: self._json_attempts(system, user, task, validate, temperature,
                                                attempts=3 if primary else 2, allow_fallback=False),
            f"task '{task}'")

    def _json_attempts(self, system, user, task, validate, temperature, attempts=3, allow_fallback=True):
        prompt = user
        last_err: Exception | None = None
        snippet = ""
        for attempt in range(attempts):
            raw = (self.complete if allow_fallback else self._complete_once)(system, prompt, temperature, True)
            try:
                data = normalize_reply(task, extract_json(raw))
                if validate:
                    validate(data)
                return data
            except (ValueError, KeyError, TypeError, IndexError) as e:
                snippet = (raw or "").strip().replace("\n", " ")[:160]
                if self.last_finish in ("length", "MAX_TOKENS"):
                    e = ValueError(f"output truncated by the model's token limit ({len(raw or '')} chars received)")
                last_err = e
                log.warning("task=%s attempt=%d invalid JSON from %s: %s | reply starts: %r", task, attempt + 1, self.label, e, snippet)
                prompt = (
                    user
                    + f"\n\nIMPORTANT: your previous reply was rejected ({e}). "
                    "Reply again with ONLY one valid JSON object that follows the schema exactly."
                )
        raise LLMError(f"{self.provider}:{self.model} did not return valid JSON for '{task}' after {attempts} attempts "
                       f"({last_err}). Reply started with: {snippet or '(empty)'}")

    def complete(self, system: str, user: str, temperature: float = 0.3, json_mode: bool = True) -> str:
        """Call the primary model; on failure retry the same request on each FALLBACK_LLM model."""
        return self._with_fallbacks(lambda primary: self._complete_once(system, user, temperature, json_mode), "completion")

    def _complete_once(self, system: str, user: str, temperature: float, json_mode: bool,
                       max_attempts: int | None = None) -> str:
        delay = 2.0
        if max_attempts is None:
            max_attempts = 3 if self._chain() else 5   # with a fallback available, move on sooner
        last = max_attempts - 1
        for attempt in range(max_attempts):
            try:
                self.calls += 1
                return self._call(system, user, temperature, json_mode)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                body = e.response.text[:300]
                if status == 429 and _is_daily_quota(body + e.response.text):
                    # A daily quota will not recover by waiting: move to the fallback model immediately.
                    raise LLMError(f"{self.provider}:{self.model} daily free quota is used up (HTTP 429). "
                                   "It resets daily; the app uses the fallback models meanwhile.") from e
                if status in RETRYABLE_STATUS and attempt < last:
                    retry_after = e.response.headers.get("retry-after")
                    hinted = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', e.response.text)  # Gemini hint
                    wait = (float(retry_after) if retry_after and retry_after.replace(".", "").isdigit()
                            else float(hinted.group(1)) if hinted else delay)
                    log.warning("%s HTTP %s, retrying in %.1fs", self.label, status, wait)
                    time.sleep(min(wait, 60) + random.random())
                    delay *= 2
                    continue
                if status in (401, 403):
                    raise LLMError(f"{self.provider} rejected the API key (HTTP {status}). Check your .env.") from e
                if status == 413:
                    raise LLMError(
                        f"Request too large for {self.provider}:{self.model} on your plan (HTTP 413, tokens-per-minute limit). "
                        "Set FALLBACK_LLM in .env (e.g. gemini:gemini-flash-latest) or use a model with a higher limit.") from e
                if status == 404:
                    raise LLMError(f"Model '{self.model}' not found on {self.provider} (HTTP 404). Set LLM_MODEL in .env.") from e
                raise LLMError(f"{self.provider} returned HTTP {status}: {body}") from e
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt < last:
                    log.warning("%s network error (%s), retrying in %.1fs", self.label, e, delay)
                    time.sleep(delay + random.random())
                    delay *= 2
                    continue
                raise LLMError(f"Could not reach {self.provider}: {e}") from e
        raise LLMError(f"{self.provider} kept failing after retries")

    # ----------------------------------------------------------------- private
    def _key(self) -> str:
        key = self.settings.key_for(self.provider)
        if not key and self.provider not in ("ollama",):
            from .config import KEY_ENV
            raise LLMError(
                f"LLM provider is '{self.provider}' but {KEY_ENV.get(self.provider, 'the API key')} is not set. "
                "Add it to your .env file (see .env.example)."
            )
        return key

    def _call(self, system: str, user: str, temperature: float, json_mode: bool) -> str:
        timeout = httpx.Timeout(self.settings.llm_timeout, connect=15.0)
        with httpx.Client(timeout=timeout) as client:
            if self.provider == "gemini":
                return self._gemini(client, system, user, temperature, json_mode)
            if self.provider == "anthropic":
                return self._anthropic(client, system, user, temperature, json_mode)
            if self.provider in OPENAI_COMPAT_BASE or self.provider == "ollama":
                return self._openai_compat(client, system, user, temperature, json_mode)
        raise LLMError(f"Unknown LLM_PROVIDER '{self.provider}'")

    def _gemini(self, client, system, user, temperature, json_mode) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        gen_cfg: dict = {"temperature": temperature, "maxOutputTokens": 16384}
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_cfg,
        }
        r = client.post(url, json=body, headers={"x-goog-api-key": self._key()})
        r.raise_for_status()
        data = r.json()
        cands = data.get("candidates") or []
        self.last_finish = (cands[0].get("finishReason") or "") if cands else ""
        if not cands:
            raise LLMError(f"Gemini returned no output (feedback: {data.get('promptFeedback')})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise LLMError(f"Gemini returned an empty reply (finishReason={cands[0].get('finishReason')})")
        return text

    def _anthropic(self, client, system, user, temperature, json_mode) -> str:
        sys_prompt = system + ("\n\nRespond with a single valid JSON object and nothing else." if json_mode else "")
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self._key(), "anthropic-version": "2023-06-01"},
            json={
                "model": self.model,
                "max_tokens": 16000,
                "system": sys_prompt,
                "temperature": temperature,
                "messages": [{"role": "user", "content": user}],
            },
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")

    def _openai_compat(self, client, system, user, temperature, json_mode) -> str:
        if self.provider == "ollama":
            base, headers = self.settings.ollama_base_url.rstrip("/") + "/v1", {}
        else:
            base, headers = OPENAI_COMPAT_BASE[self.provider], {"Authorization": f"Bearer {self._key()}"}
        body: dict = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system + ("\n\nRespond with a single valid JSON object." if json_mode else "")},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.provider == "groq":
            body["max_completion_tokens"] = 3500   # keeps (input + output) under free-tier per-minute limits
            if "gpt-oss" in self.model:
                body["reasoning_effort"] = "low"   # hidden reasoning tokens count against the TPM limit
        r = client.post(f"{base}/chat/completions", headers=headers, json=body)
        if r.status_code == 400 and json_mode and "json_validate_failed" in r.text:
            # Groq's strict JSON validator rejected the output; ask again without it and parse ourselves.
            body.pop("response_format", None)
            r = client.post(f"{base}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        choice = r.json()["choices"][0]
        self.last_finish = choice.get("finish_reason") or ""
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            # e.g. the model spent its whole budget on hidden reasoning (finish_reason=length)
            raise LLMError(f"{self.provider}:{self.model} returned an empty reply (finish_reason={choice.get('finish_reason')})")
        return content
