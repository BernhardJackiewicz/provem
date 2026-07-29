"""Optional, key-gated OpenAI answer provider (stdlib urllib, no dependency).

A thin ``str -> str`` callable for :class:`cognitive_memory.answerer.LLMAnswerer`.
The package never requires an LLM SDK; this provider is only constructed when the
caller opts into ``--answer-mode llm`` and supplies ``OPENAI_API_KEY``. The key
is read from the environment and never logged or persisted.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional


class LlmAnswerProviderError(RuntimeError):
    pass


class OpenAIAnswerProvider:
    """Calls the OpenAI Chat Completions API and returns the message text."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 60,
        temperature: float = 0.0,
    ) -> None:
        if not api_key:
            raise LlmAnswerProviderError("OPENAI_API_KEY is required for --answer-mode llm.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.call_count = 0

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> "OpenAIAnswerProvider":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=model or os.environ.get("OPENAI_ANSWER_MODEL", "") or os.environ.get("OPENAI_MODEL", "") or "gpt-4o-mini",
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )

    def _url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return "%s/chat/completions" % self.base_url
        return "%s/v1/chat/completions" % self.base_url

    def __call__(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        body = self._post(messages, include_temperature=self.temperature is not None)
        self.call_count += 1
        choices = body.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "")).strip()

    def _post(self, messages: list, include_temperature: bool) -> dict:
        payload = {"model": self.model, "messages": messages}
        if include_temperature:
            payload["temperature"] = self.temperature
        request = urllib.request.Request(
            self._url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer %s" % self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # some newer models reject a non-default temperature -> retry without it
            if include_temperature and exc.code == 400 and "temperature" in detail:
                return self._post(messages, include_temperature=False)
            raise LlmAnswerProviderError("OpenAI answer request failed: HTTP %s %s" % (exc.code, detail[:300])) from exc
        except urllib.error.URLError as exc:
            raise LlmAnswerProviderError("OpenAI answer request failed: %s" % exc.reason) from exc
