"""Deterministic LOTUS stand-in for offline tests (never calls a model)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd  # pyright: ignore[reportMissingTypeStubs]

# Tests read what each operation was handed, and push what the next one returns.
calls: list[dict[str, Any]] = []
frames: list[Any] = []
failures: list[Exception] = []

OPERATIONS = ("sem_filter", "sem_map", "sem_extract", "sem_agg", "sem_topk", "sem_join")


def reset() -> None:
    """Empty every hook, so one test's script cannot reach the next."""
    calls.clear()
    frames.clear()
    failures.clear()
    settings.lm.stats.physical_usage = _Usage()


class _Usage:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.total_cost = 0.0


class _Stats:
    def __init__(self) -> None:
        self.physical_usage = _Usage()


class LM:
    """The model LOTUS runs its operations on."""

    def __init__(self, model: str = "gpt-4.1", **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs
        self.stats = _Stats()


class _Settings:
    def __init__(self) -> None:
        self.lm: Any = LM()

    def configure(self, lm: Any) -> None:
        self.lm = lm


settings = _Settings()
models = SimpleNamespace(LM=LM)


def _operation(name: str) -> type:
    class _Operation:
        def __init__(self, frame: Any) -> None:
            self._frame = frame

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            calls.append({"operation": name, "frame": self._frame, "args": args, "kwargs": kwargs})
            if failures:
                raise failures.pop(0)
            # The real suite spends tokens on its own model, and the adapter reports the
            # difference across one call, so a stubbed call has to move these numbers.
            usage = settings.lm.stats.physical_usage
            usage.prompt_tokens += 7
            usage.completion_tokens += 3
            usage.total_tokens += 10
            usage.total_cost += 0.001
            return frames.pop(0) if frames else self._frame

    return _Operation


for _name in OPERATIONS:
    # The real package registers its operations the same way, at import time.
    pd.api.extensions.register_dataframe_accessor(_name)(_operation(_name))
