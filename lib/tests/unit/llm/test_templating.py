"""Behavior tests for prompt rendering, request building, and response extraction."""

from __future__ import annotations

from typing import Any

import jinja2
import pytest

from blue_data_planner.llm.templating import build_message, extract_output, render_prompt

BASE: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.api": "ChatCompletion",
    "openai.model": "gpt-4o",
    "input_json": '[{"role": "user"}]',
    "input_context": "$[0]",
    "input_context_field": "content",
    "input_field": "messages",
    "output_path": "$.choices[0].message.content",
}


def test_message_build_end_to_end() -> None:
    message = build_message("DATA", BASE)
    assert message["api"] == "ChatCompletion"  # prefix-stripped api property
    assert message["model"] == "gpt-4o"
    assert message["messages"] == [{"role": "user", "content": "DATA"}]


OPENAI_PAYLOAD: dict[str, Any] = {
    "choices": [{"message": {"role": "assistant", "content": "  42  "}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


def test_output_path_extraction() -> None:
    assert extract_output(OPENAI_PAYLOAD, {"output_path": BASE["output_path"]}) == "  42  "


def test_output_strip_and_cast_int() -> None:
    props = {"output_path": BASE["output_path"], "output_strip": True, "output_cast": "int"}
    assert extract_output(OPENAI_PAYLOAD, props) == 42


def test_output_cast_bool_is_a_no_op() -> None:
    # Only int/float/json/str are implemented; "bool" falls through unchanged.
    payload = {"choices": [{"message": {"content": "false"}}]}
    props = {"output_path": "$.choices[0].message.content", "output_cast": "bool"}
    assert extract_output(payload, props) == "false"


def test_output_cast_json() -> None:
    payload = {"choices": [{"message": {"content": '{"a": 1}'}}]}
    props = {"output_path": "$.choices[0].message.content", "output_cast": "json"}
    assert extract_output(payload, props) == {"a": 1}


def test_output_replace_transformation() -> None:
    payload = {"choices": [{"message": {"content": "a-b-c"}}]}
    props = {
        "output_path": "$.choices[0].message.content",
        "output_transformations": [{"transformation": "replace", "from": "-", "to": "_"}],
    }
    assert extract_output(payload, props) == "a_b_c"


def test_output_sub_transformation_fixed() -> None:
    # A sub transformation uses the configured replacement string.
    payload = {"choices": [{"message": {"content": "x12y34"}}]}
    props = {
        "output_path": "$.choices[0].message.content",
        "output_transformations": [{"transformation": "sub", "from": r"\d+", "to": "#"}],
    }
    assert extract_output(payload, props) == "x#y#"


def test_json_set_old_index_surface() -> None:
    # jsonpath-ng 1.5.x exposes Index.index; modern versions expose indices.
    # The setter must handle BOTH: simulate the old surface directly.
    from blue_data_planner.llm.templating import _json_set  # pyright: ignore[reportPrivateUsage]

    class _OldIndex:
        def __init__(self, index: int) -> None:
            self.index = index

    class _OldMatch:
        def __init__(self, index: int) -> None:
            self.path = _OldIndex(index)

    import blue_data_planner.llm.templating as templating

    target: list[dict[str, str]] = [{"role": "user"}]
    original_find = templating._find  # pyright: ignore[reportPrivateUsage]
    templating._find = lambda path, tgt: [_OldMatch(0)]  # type: ignore[assignment]
    try:
        _json_set(target, "$[0]", "content", "hello")
    finally:
        templating._find = original_find  # type: ignore[assignment]
    assert target == [{"role": "user", "content": "hello"}]


def test_json_set_installed_index_surface() -> None:
    # The installed jsonpath-ng (modern API) end-to-end through build_message.
    message = build_message("hello", BASE)
    assert message["messages"] == [{"role": "user", "content": "hello"}]


def test_missing_output_path_yields_none() -> None:
    assert (
        extract_output({"nothing": True}, {"output_path": "$.choices[0].message.content"}) is None
    )


@pytest.mark.parametrize(
    "value",
    ["{{ other }}", "{% if other %}yes{% endif %}", "a {# b", "about $other and ${other}"],
)
def test_a_rendered_prompt_carries_each_value_verbatim(value: str) -> None:
    """A value holding Jinja or string.Template syntax reaches the prompt unchanged."""
    rendered = render_prompt("[{{ value }}]", value=value, other="SUBSTITUTED")
    assert rendered == f"[{value}]"


def test_a_prompt_placeholder_with_no_value_raises() -> None:
    """Rendering a prompt that names a value the caller did not pass raises."""
    with pytest.raises(jinja2.UndefinedError):
        render_prompt("{{ request }} {{ reason }}", request="r")
