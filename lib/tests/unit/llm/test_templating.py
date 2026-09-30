"""Behavior tests for prompt rendering, request building, and response extraction."""

from __future__ import annotations

from typing import Any

import jinja2
import pytest

from blue_data_planner.llm.templating import build_message, extract_output, render_prompt

BASE: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.api": "ChatCompletion",
    "openai.model": "gpt-6-luna",
    "input_json": '[{"role": "user"}]',
    "input_context": "$[0]",
    "input_context_field": "content",
    "input_field": "messages",
    "output_path": "$.choices[0].message.content",
}


def test_a_built_message_carries_the_api_model_and_prompt() -> None:
    """The message has the prefix-stripped api and model, and the prompt in the input field."""
    message = build_message("DATA", BASE)
    assert message["api"] == "ChatCompletion"  # prefix-stripped api property
    assert message["model"] == "gpt-6-luna"
    assert message["messages"] == [{"role": "user", "content": "DATA"}]


OPENAI_PAYLOAD: dict[str, Any] = {
    "choices": [{"message": {"role": "assistant", "content": "  42  "}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


def test_the_output_path_selects_the_answer_from_the_response() -> None:
    """The text at the output path is returned as written, whitespace included."""
    assert extract_output(OPENAI_PAYLOAD, {"output_path": BASE["output_path"]}) == "  42  "


@pytest.mark.parametrize(
    "content,extra_props,expected",
    [
        ("  42  ", {"output_strip": True, "output_cast": "int"}, 42),
        ('{"a": 1}', {"output_cast": "json"}, {"a": 1}),
    ],
    ids=["strip-and-int", "json"],
)
def test_output_options_convert_the_extracted_text_to_the_named_type(
    content: str, extra_props: dict[str, Any], expected: Any
) -> None:
    """Stripping and casting the text at the output path gives a value of the cast type."""
    payload = {"choices": [{"message": {"content": content}}]}
    props = {"output_path": BASE["output_path"], **extra_props}
    assert extract_output(payload, props) == expected


def test_an_output_cast_to_bool_leaves_the_text_unchanged() -> None:
    """Only int, float, json, and str are cast; a bool cast returns the text as it was."""
    # Only int/float/json/str are implemented; "bool" falls through unchanged.
    payload = {"choices": [{"message": {"content": "false"}}]}
    props = {"output_path": "$.choices[0].message.content", "output_cast": "bool"}
    assert extract_output(payload, props) == "false"


@pytest.mark.parametrize(
    "content,transformation,expected",
    [
        ("a-b-c", {"transformation": "replace", "from": "-", "to": "_"}, "a_b_c"),
        ("x12y34", {"transformation": "sub", "from": r"\d+", "to": "#"}, "x#y#"),
    ],
    ids=["replace", "sub"],
)
def test_an_output_transformation_rewrites_the_extracted_text(
    content: str, transformation: dict[str, str], expected: str
) -> None:
    """A configured replace or sub transformation applies its from and to strings."""
    payload = {"choices": [{"message": {"content": content}}]}
    props = {"output_path": BASE["output_path"], "output_transformations": [transformation]}
    assert extract_output(payload, props) == expected


def test_setting_a_value_works_with_the_old_jsonpath_index_surface() -> None:
    """The setter handles the jsonpath-ng 1.5 match shape, which exposes index, not indices."""
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


def test_building_a_message_sets_the_prompt_with_the_installed_jsonpath_version() -> None:
    """The installed jsonpath-ng puts the prompt into the input field."""
    # The installed jsonpath-ng (modern API) end-to-end through build_message.
    message = build_message("hello", BASE)
    assert message["messages"] == [{"role": "user", "content": "hello"}]


def test_a_response_without_the_output_path_yields_none() -> None:
    """A response holding nothing at the output path gives None."""
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
