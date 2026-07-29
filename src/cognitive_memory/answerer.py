"""Pluggable answer synthesis over governed recall results.

Governed recall returns the *right memory* (or a safe abstention). Turning that
memory into a concise natural-language answer is a separate, swappable step:

- :class:`ExtractiveAnswerer` -- keyless default; pulls a short span from the
  memory text (wraps :mod:`answer`). No API key, deterministic.
- :class:`LLMAnswerer` -- optional, key-gated. Takes an injected ``llm`` callable
  (``str -> str``) so the package never imports or requires any LLM SDK; the
  caller owns the key and the model. This is the documented path that converts
  the doubled retrieval recall into end-to-end answer accuracy.

Both honor governance: they only ever answer from memories the governance layer
already cleared, and they abstain (return ``""``) rather than invent an answer.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Protocol, runtime_checkable

from .answer import extractive_span, question_type


@runtime_checkable
class Answerer(Protocol):
    def answer(self, question: str, memories: List[Any]) -> str:
        """Return a concise answer, or "" to abstain."""
        ...


def _memory_text(memory: Any) -> str:
    # accept both MemoryRecord-like (has .text/.object) and dicts
    for attr in ("object", "text", "claim"):
        value = getattr(memory, attr, None)
        if value:
            return str(value)
    if isinstance(memory, dict):
        for key in ("object", "text", "claim"):
            if memory.get(key):
                return str(memory[key])
    return str(memory)


class ExtractiveAnswerer:
    """Keyless: prefer a structured object value, else extract a short span."""

    def __init__(self, max_tokens: int = 12) -> None:
        self.max_tokens = max_tokens

    def answer(self, question: str, memories: List[Any]) -> str:
        memories = [m for m in memories if m is not None and _memory_text(m).strip()]
        if not memories:
            return ""
        top = memories[0]
        obj = getattr(top, "object", None)
        if not obj and isinstance(top, dict):
            obj = top.get("object")
        if obj:
            return str(obj)
        qtype = question_type(question)
        for memory in memories:
            span = extractive_span(question, _memory_text(memory), max_tokens=self.max_tokens, qtype=qtype)
            if span:
                return span
        return ""


# A prompt template kept small and explicit so the LLM answers ONLY from the
# governed memories and abstains otherwise (no outside knowledge, no invention).
_LLM_PROMPT = (
    "You are a memory answerer. Answer the question using ONLY the memories "
    "below. If the memories do not contain the answer, reply exactly with "
    "ABSTAIN. Answer with the shortest exact span.\n\n"
    "Question: {question}\n\nMemories:\n{memories}\n\nAnswer:"
)


class LLMAnswerer:
    """Optional key-gated answerer. The caller injects the LLM callable.

    ``llm`` is any ``str -> str`` function (an Anthropic/OpenAI wrapper, a local
    model, etc.). The package requires no LLM dependency; callers own the key.
    ``fallback`` (default ExtractiveAnswerer) is used if the LLM abstains/errors.
    """

    def __init__(self, llm: Callable[[str], str], fallback: Optional[Answerer] = None, max_memories: int = 8) -> None:
        self.llm = llm
        self.fallback = fallback or ExtractiveAnswerer()
        self.max_memories = max_memories

    def answer(self, question: str, memories: List[Any]) -> str:
        memories = [m for m in memories if m is not None and _memory_text(m).strip()]
        if not memories:
            return ""
        rendered = "\n".join("- %s" % _memory_text(m) for m in memories[: self.max_memories])
        prompt = _LLM_PROMPT.format(question=question, memories=rendered)
        try:
            out = (self.llm(prompt) or "").strip()
        except Exception:
            out = ""
        if not out or out.upper() == "ABSTAIN":
            return self.fallback.answer(question, memories)
        return out
