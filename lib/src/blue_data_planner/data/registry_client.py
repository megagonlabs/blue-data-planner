from __future__ import annotations

from typing import Any, cast

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)


class DataRegistryClient:
    """Everything this package does with a blue-data DataRegistry."""

    def __init__(self, registry: DataRegistry) -> None:
        # The one untyped-library boundary (blue-data has no py.typed): delegate
        # through Any so unknown member types stop here, not in every caller.
        self._delegate: Any = registry

    def get_source_properties(self, source: str) -> Any:
        return self._delegate.get_source_properties(source)

    def connect_source(self, source: str) -> Any:
        return self._delegate.connect_source(source)

    def execute_query(
        self,
        query: Any,
        source: str,
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> Any:
        # The facade accepts (query, source, ...) while blue-data accepts
        # (source, query, ...). Keyword delegation prevents a positional swap;
        # blue-data normalizes falsy optional_properties to an empty mapping.
        return self._delegate.execute_query(
            source=source,
            query=query,
            database=database,
            collection=collection,
            optional_properties=optional_properties,
        )

    def get_data_source_schema(
        self,
        source: str,
        database: str,
        collection: str,
        format: str = "dict",  # noqa: A002 — blue-data's own parameter name
    ) -> Any:
        return self._delegate.get_data_source_schema(
            source=source,
            database=database,
            collection=collection,
            format=format,
        )

    def search_records(
        self,
        input_query: Any,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 5,
    ) -> list[dict[str, Any]]:
        results = self._delegate.search_records(
            input_query, type=type, scope=scope, page=page, page_size=page_size
        )
        return [_normalize_search_result(raw) for raw in results]


# The wrapped registry's keyword engine scores a name hit 10 and a description hit 5;
# 15 is its maximum possible raw score.
_KEYWORD_MAX_SCORE = 15.0


def _normalize_search_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a delegate search result to the consumer's flat search shape.

    The delegate returns one of two shapes, both ordered goodness-DESCENDING:
    the flat variant ({...record fields..., "_score": combined in [0, 1]}) from the
    hybrid engine, and the wrapped variant ({"record": {...}, "score": int}) from
    the keyword fallback. The consumer contract is a flat record with an INVERTED
    score (lower = better, ascending order — the same inversion the original
    registry applies to the same score combination): flat => 1 - _score (a missing
    _score counts as 0, the worst); wrapped => 1 - min(1, score / 15) with 15 the
    keyword engine's maximum. Normalization preserves list order, which is exactly
    consumer-ascending. Delegate records carry no id field; the id is derived with
    the delegate's own hierarchy-node convention "{scope}/{type}/{name}".
    """
    if isinstance(raw.get("record"), dict) and "score" in raw:
        record = cast("dict[str, Any]", raw["record"])
        score = 1.0 - min(1.0, float(raw.get("score", 0.0)) / _KEYWORD_MAX_SCORE)
    else:
        record = {key: value for key, value in raw.items() if not key.startswith("_")}
        score = 1.0 - float(raw.get("_score", 0.0))
    normalized: dict[str, Any] = {
        "name": record.get("name", ""),
        "type": record.get("type", ""),
        "scope": record.get("scope", "/"),
        "id": f"{record.get('scope', '/')}/{record.get('type', '')}/{record.get('name', '')}",
        "score": score,
    }
    if "description" in record:
        normalized["description"] = record["description"]
    return normalized
