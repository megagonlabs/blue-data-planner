"""Deterministic litellm stand-in for offline tests (never calls a network)."""

from __future__ import annotations

from typing import Any, cast

calls: list[dict[str, Any]] = []
fail_next: list[Exception] = []  # tests push exceptions to simulate provider failures


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


def completion(**kwargs: Any) -> _Response:
    calls.append(kwargs)
    if fail_next:
        raise fail_next.pop(0)
    prompt = ""
    messages: list[Any] = kwargs.get("messages") or []
    if messages and isinstance(messages[0], dict):
        first = cast("dict[str, Any]", messages[0])
        prompt = str(first.get("content", ""))
    return _Response(
        {
            "choices": [{"message": {"role": "assistant", "content": f"echo:{prompt}"}}],
            "usage": {"prompt_tokens": len(prompt.split()), "completion_tokens": 3},
            "model": kwargs.get("model"),
        }
    )


def completion_cost(completion_response: Any) -> float:
    return 0.000123


inflight = {"now": 0, "max": 0}


async def acompletion(**kwargs: Any) -> _Response:
    import asyncio

    inflight["now"] += 1
    inflight["max"] = max(inflight["max"], inflight["now"])
    try:
        await asyncio.sleep(0.02)
        return completion(**kwargs)
    finally:
        inflight["now"] -= 1
