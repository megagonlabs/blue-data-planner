from __future__ import annotations

import copy
import json
import re
from typing import Any

from jsonpath_ng.ext import (  # pyright: ignore[reportMissingTypeStubs]
    parse as _jsonpath_parse,  # pyright: ignore[reportUnknownVariableType]
)


def _find(path: str, target: Any) -> list[Any]:
    # The one untyped jsonpath-ng boundary: matches typed as Any past this point.
    expression: Any = _jsonpath_parse(path)  # pyright: ignore[reportUnknownVariableType]
    return list(expression.find(target))


def render_prompt(template: str, **values: Any) -> str:
    """Render a Jinja prompt template once, passing every value through verbatim."""
    # Values are model output, user input, or registry text, so they are never parsed as
    # templates, and a placeholder with no value raises rather than rendering empty.
    from jinja2 import BaseLoader, Environment, StrictUndefined

    environment = Environment(loader=BaseLoader(), undefined=StrictUndefined)
    return environment.from_string(template).render(**values)


def build_message(
    input_data: Any, properties: dict[str, Any], default_prefix: str = ""
) -> dict[str, Any]:
    """Build a request message carrying the input where the client's properties place it."""
    message: dict[str, Any] = api_properties(properties, default_prefix)

    input_object: Any = input_data
    if properties.get("input_json") is not None:
        raw = properties["input_json"]
        input_object = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
        _json_set(
            input_object,
            properties["input_context"],
            properties["input_context_field"],
            input_data,
        )

    message[properties["input_field"]] = input_object
    return message


def extract_output(payload: Any, properties: dict[str, Any]) -> Any:
    """Extract and post-process the configured value from a provider response."""
    matches: list[Any] = [m.value for m in _find(properties["output_path"], payload)]
    output_data: Any = matches[0] if matches else None

    output_data = _preprocess_output(output_data, properties)
    return _cast_output(output_data, properties)


def api_properties(properties: dict[str, Any], default_prefix: str = "") -> dict[str, Any]:
    """Return the provider fields a client's `<prefix>.*` properties carry."""
    # The client name supplies the prefix when service_prefix is absent.
    prefix = str(properties.get("service_prefix", default_prefix)) + "."
    if prefix == ".":
        return {}
    return {
        key[len(prefix) :]: value for key, value in properties.items() if key.startswith(prefix)
    }


def _json_set(target: Any, context_path: str, field: str, value: Any) -> None:
    # jsonpath-ng renamed Index.index to indices; support both forms for the one shape
    # used here: a root-list index such as "$[0]" followed by a field assignment.
    for match in _find(context_path, target):
        path: Any = match.path
        index = getattr(path, "index", None)
        if index is None:
            indices = getattr(path, "indices", None)
            index = indices[0] if indices else None
        if index is not None and isinstance(target, list):
            target[index][field] = value


def _preprocess_output(output_data: Any, properties: dict[str, Any]) -> Any:
    if isinstance(output_data, str):
        if "output_strip" in properties:
            output_data = output_data.strip()
        if "output_transformations" in properties:
            for transformation in properties["output_transformations"]:
                kind = transformation["transformation"]
                if kind == "replace":
                    output_data = output_data.replace(transformation["from"], transformation["to"])
                elif kind == "sub":
                    # The replacement must be the configured output string, not the
                    # compiled search expression.
                    output_data = re.sub(
                        re.compile(transformation["from"]), transformation["to"], output_data
                    )
    return output_data


def _cast_output(output_data: Any, properties: dict[str, Any]) -> Any:
    # Only int/float/json/str are implemented. Other cast names, including "bool",
    # deliberately leave the value unchanged.
    if "output_cast" in properties:
        cast = str(properties["output_cast"]).lower()
        if cast == "int":
            output_data = int(output_data)
        elif cast == "float":
            output_data = float(output_data)
        elif cast == "json":
            output_data = json.loads(output_data)
        elif cast == "str":
            output_data = str(output_data)
    return output_data
