from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from blue_data_planner.llm.templating import api_properties, build_message, extract_output
from blue_data_planner.types import Conversation, InvalidSettingError, LLMError, LLMUsage, Turn

M = TypeVar("M", bound=BaseModel)

# The closed adapter-override key set; unknown llm.* keys are rejected (InvalidSettingError).
_ACCEPTED_LLM_KEYS = {
    "model",
    "stream",
    "max_tokens",
    "temperature",
    "cache",
    "cache_dir",
    "max_parallel",
    "rate_limit",
}
# Adapter-control keys: consumed by the client, never forwarded to the provider.
_CONTROL_KEYS = {"cache", "cache_dir", "max_parallel", "rate_limit"}
# Strip dispatch-only and transport-resolution fields after all merges so an
# override cannot forward them to the provider.
_RESERVED_FIELDS = {"api", "service_url"}


@dataclass
class UsageRecord:
    model: str
    # None when the provider did not report it, or litellm holds no price for the model.
    prompt_tokens: int | None = 0
    completion_tokens: int | None = 0
    cost: float | None = 0.0
    latency_seconds: float = 0.0
    cache_hit: bool = False


class UsageLedger:
    """One record per execute_api_call (cache hits included, end-to-end latency)."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)

    @property
    def total_cost(self) -> float | None:
        costs = [record.cost for record in self.records]
        return None if None in costs else sum(cost for cost in costs if cost is not None)

    @property
    def total_tokens(self) -> int | None:
        counts = [
            count
            for record in self.records
            for count in (record.prompt_tokens, record.completion_tokens)
        ]
        return None if None in counts else sum(count for count in counts if count is not None)


class LLMClient:
    """Build templated requests and execute them through an LLM provider.

    OpenAI-shaped responses feed the configured output-path extraction. The client
    also provides per-call usage records, async execution, bounded concurrency,
    caching, and rate limiting.
    """

    def __init__(self, name: str, properties: dict[str, Any] | None = None) -> None:
        self.name = name
        self.properties: dict[str, Any] = dict(properties or {})
        _check_settings(self.properties, name.lower())
        self.ledger = UsageLedger()
        self.last_usage: UsageRecord | None = None
        self._caches: dict[str, Any] = {}  # cache_dir -> diskcache.Cache
        self._semaphores: dict[int, asyncio.Semaphore] = {}
        self._rate_lock = threading.Lock()
        self._last_call_at = 0.0

    # ------------------------------------------------------------------ sync --
    def execute_api_call(
        self,
        input: Any,  # noqa: A002 — public call-surface parameter
        properties: dict[str, Any] | None = None,
    ) -> Any:
        started = time.monotonic()
        merged = {**self.properties, **(properties or {})}
        if properties:
            _check_settings(merged, self.name.lower())
        message = build_message(input, merged, self.name.lower())
        kwargs = self._build_call_kwargs(message, merged)

        cache, cache_key = self._cache_lookup(merged, kwargs)
        if cache is not None and cache_key in cache:
            payload = cache[cache_key]
            self._record(kwargs, payload, started, cache_hit=True)
            return extract_output(payload, merged)

        self._apply_rate_limit(merged)
        payload, cost = self._call_provider(kwargs)
        if cache is not None and cache_key is not None:
            cache[cache_key] = payload
        self._record(kwargs, payload, started, cache_hit=False, cost=cost)
        return extract_output(payload, merged)

    def complete_structured(self, conversation: Conversation, response_model: type[M]) -> M:
        """Answer the conversation with a value shaped by the model, recording the turn and cost."""
        started = time.monotonic()
        message = api_properties(self.properties, self.name.lower())
        message["messages"] = [
            {"role": turn.role, "content": turn.content} for turn in conversation.turns
        ]
        kwargs = self._build_call_kwargs(message, self.properties)
        kwargs["response_format"] = response_model
        self._apply_rate_limit(self.properties)
        payload, cost = self._call_provider(kwargs)
        record = self._record(kwargs, payload, started, cache_hit=False, cost=cost)
        content = _message_content(payload)
        try:
            value = response_model.model_validate_json(content)
        except ValidationError as error:
            raise LLMError(
                f"the provider did not answer in the shape {response_model.__name__} asks for: "
                f"{error}",
                code="bad_response",
            ) from error
        conversation.turns.append(Turn(role="assistant", content=content))
        # Read off the record _record just built rather than self.last_usage: two calls
        # sharing one client across threads could otherwise interleave between another
        # call's write and this read, and silently record the wrong call's cost.
        conversation.usage.append(
            LLMUsage(
                model=record.model,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                cost=record.cost,
                latency_seconds=record.latency_seconds,
            )
        )
        return value

    # ----------------------------------------------------------------- async --
    async def aexecute_api_call(
        self,
        input: Any,  # noqa: A002 — matches the synchronous call surface
        properties: dict[str, Any] | None = None,
    ) -> Any:
        started = time.monotonic()
        merged = {**self.properties, **(properties or {})}
        if properties:
            _check_settings(merged, self.name.lower())
        message = build_message(input, merged, self.name.lower())
        kwargs = self._build_call_kwargs(message, merged)

        cache, cache_key = self._cache_lookup(merged, kwargs)
        if cache is not None and cache_key in cache:
            payload = cache[cache_key]
            self._record(kwargs, payload, started, cache_hit=True)
            return extract_output(payload, merged)

        semaphore = self._resolve_semaphore(merged)
        async with semaphore:
            self._apply_rate_limit(merged)
            payload, cost = await self._acall_provider(kwargs)
        if cache is not None and cache_key is not None:
            cache[cache_key] = payload
        self._record(kwargs, payload, started, cache_hit=False, cost=cost)
        return extract_output(payload, merged)

    # ------------------------------------------------------------- internals --
    def _build_call_kwargs(
        self, message: dict[str, Any], properties: dict[str, Any]
    ) -> dict[str, Any]:
        kwargs = dict(message)  # the extracted <prefix>.* fields + input_field payload

        # messages: the wrapped input object built by templating (every operator's shape)
        input_field = properties.get("input_field", "messages")
        if input_field != "messages" and input_field in kwargs:
            kwargs["messages"] = kwargs.pop(input_field)

        # _check_settings has already refused any llm.* key outside _ACCEPTED_LLM_KEYS.
        for key, value in properties.items():
            if key.startswith("llm.") and key[4:] not in _CONTROL_KEYS:
                kwargs[key[4:]] = value

        # Remove transport-resolution fields after every override has been merged.
        for reserved in _RESERVED_FIELDS:
            kwargs.pop(reserved, None)
        return kwargs

    def _cache_lookup(
        self, properties: dict[str, Any], kwargs: dict[str, Any]
    ) -> tuple[Any | None, str | None]:
        if not properties.get("llm.cache"):
            return None, None
        # Caches are bound per directory so a per-call llm.cache_dir override selects
        # its own isolated cache instead of silently reusing the first one opened.
        cache_dir = str(properties.get("llm.cache_dir", ".llm_cache"))
        cache = self._caches.get(cache_dir)
        if cache is None:
            import diskcache  # pyright: ignore[reportMissingTypeStubs]

            cache = diskcache.Cache(cache_dir)
            self._caches[cache_dir] = cache
        return cache, json.dumps(kwargs, sort_keys=True, default=str)

    def _resolve_semaphore(self, properties: dict[str, Any]) -> asyncio.Semaphore:
        max_parallel = int(properties.get("llm.max_parallel", 8))
        loop_id = id(asyncio.get_running_loop())
        key = hash((loop_id, max_parallel))
        semaphore = self._semaphores.get(key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(max_parallel)
            self._semaphores[key] = semaphore
        return semaphore

    def _apply_rate_limit(self, properties: dict[str, Any]) -> None:
        rate = properties.get("llm.rate_limit")
        if not rate:
            return
        min_interval = 1.0 / float(rate)  # calls per second
        with self._rate_lock:
            wait_for = self._last_call_at + min_interval - time.monotonic()
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_call_at = time.monotonic()

    def _record(
        self,
        kwargs: dict[str, Any],
        payload: dict[str, Any],
        started: float,
        cache_hit: bool,
        cost: float | None = 0.0,
    ) -> UsageRecord:
        # A cache hit reaches no provider, so it spent nothing. Otherwise the usage block was
        # checked when the response was materialized.
        usage: dict[str, Any] = {} if cache_hit else payload["usage"]
        record = UsageRecord(
            model=str(kwargs.get("model")),
            prompt_tokens=0 if cache_hit else usage.get("prompt_tokens"),
            completion_tokens=0 if cache_hit else usage.get("completion_tokens"),
            cost=0.0 if cache_hit else cost,
            latency_seconds=time.monotonic() - started,  # end-to-end, incl. retries
            cache_hit=cache_hit,
        )
        self.last_usage = record
        self.ledger.add(record)
        return record

    @retry(
        retry=retry_if_exception_type(LLMError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, max=2),
        reraise=True,
    )
    def _call_provider(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
        import litellm  # lazy, so the default test run can put its stub in place first

        try:
            response: Any = litellm.completion(**kwargs)
        except Exception as e:  # transport boundary: wrap provider errors
            raise LLMError(str(e), code="provider_error", provider_error=e) from e
        return self._materialize(response)

    @retry(
        retry=retry_if_exception_type(LLMError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, max=2),
        reraise=True,
    )
    async def _acall_provider(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
        import litellm  # lazy — same boundary as the sync path

        try:
            response: Any = await litellm.acompletion(**kwargs)
        except Exception as e:
            raise LLMError(str(e), code="provider_error", provider_error=e) from e
        return self._materialize(response)

    def _materialize(self, response: Any) -> tuple[dict[str, Any], float | None]:
        # Everything between the provider return and a validated OpenAI-shaped dict is
        # provider-response territory: failures here are wrapped, chained LLMErrors.
        try:
            raw: Any = response.model_dump() if hasattr(response, "model_dump") else response
        except Exception as e:
            raise LLMError(
                "provider response could not be materialized", code="bad_response", provider_error=e
            ) from e
        raw_dict: dict[str, Any] | None = (
            cast("dict[str, Any]", raw) if isinstance(raw, dict) else None
        )
        if raw_dict is None or not isinstance(raw_dict.get("choices"), list):
            raise LLMError(
                "provider returned a non-OpenAI-shaped response (no choices list)",
                code="bad_response",
            )
        payload: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
        _check_usage(payload.get("usage"))
        return payload, self._completion_cost(response)

    @staticmethod
    def _completion_cost(response: Any) -> float | None:
        try:
            import litellm

            return float(litellm.completion_cost(completion_response=response))
        except Exception:
            # litellm raises a bare Exception for a model it holds no price for. The call itself
            # succeeded, so its cost is recorded as unknown rather than failing the call.
            return None


def _check_usage(usage: Any) -> None:
    # A count the provider leaves out is unknown; one it sends in a shape no count has is a
    # malformed response, like any other.
    if not isinstance(usage, dict):
        raise LLMError("provider response carries no usage block", code="bad_response")
    for key in ("prompt_tokens", "completion_tokens"):
        count: Any = cast("dict[str, Any]", usage).get(key)
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0
        ):
            raise LLMError(
                f"provider reported {key} as {count!r}, which is not a token count",
                code="bad_response",
            )


def _check_settings(properties: dict[str, Any], default_prefix: str) -> None:
    # Every value checked here is fixed before the first call, so a mistake in it is refused
    # while the client is built rather than reported later as a provider failure.
    for key in properties:
        if key.startswith("llm.") and key[4:] not in _ACCEPTED_LLM_KEYS:
            raise InvalidSettingError(
                f"unknown adapter override '{key}' — accepted: "
                f"{sorted('llm.' + k for k in _ACCEPTED_LLM_KEYS)}"
            )
    if not (properties.get("llm.model") or api_properties(properties, default_prefix).get("model")):
        raise InvalidSettingError("no model configured (set <prefix>.model or llm.model)")
    if properties.get("llm.cache"):
        try:
            import diskcache  # noqa: F401  # pyright: ignore[reportMissingTypeStubs, reportUnusedImport]
        except ImportError as error:
            raise InvalidSettingError(
                "llm.cache requested but diskcache is not installed (pip install diskcache)"
            ) from error


def _message_content(payload: dict[str, Any]) -> str:
    # _materialize has already checked that "choices" is a list; everything below it is the
    # provider's shape and is checked here rather than assumed.
    choices = cast("list[Any]", payload["choices"])
    if not choices:
        raise LLMError("the provider returned no choices", code="bad_response")
    first = choices[0]
    message = cast("dict[str, Any]", first).get("message") if isinstance(first, dict) else None
    content = cast("dict[str, Any]", message).get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LLMError("the provider returned no message content", code="bad_response")
    return content
