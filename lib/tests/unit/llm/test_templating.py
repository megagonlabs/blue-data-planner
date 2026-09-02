"""Behavior tests for request templating and response extraction."""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_planner.llm.templating import build_message, extract_output

BASE: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.api": "ChatCompletion",
    "openai.model": "gpt-4o",
    "input_template": "Answer about $topic using $input",
    "input_json": '[{"role": "user"}]',
    "input_context": "$[0]",
    "input_context_field": "content",
    "input_field": "messages",
    "output_path": "$.choices[0].message.content",
}


def test_message_build_end_to_end() -> None:
    message = build_message("DATA", BASE, {"topic": "jobs"})
    assert message["api"] == "ChatCompletion"  # prefix-stripped api property
    assert message["model"] == "gpt-4o"
    assert message["messages"] == [{"role": "user", "content": "Answer about jobs using DATA"}]


def test_unknown_placeholder_survives() -> None:
    props = {**BASE, "input_template": "keep $missing and use $input"}
    message = build_message("X", props, {})
    assert message["messages"][0]["content"] == "keep $missing and use X"


@pytest.mark.parametrize(
    "properties_key,additional",
    [
        ("input", {}),  # properties vs input params
        ("topic", {"topic": "b"}),  # properties vs additional
    ],
    ids=["properties-vs-input", "properties-vs-additional"],
)
def test_collisions_raise_type_error(properties_key: str, additional: dict[str, Any]) -> None:
    props = {**BASE, properties_key: "a"}
    with pytest.raises(TypeError, match="multiple values"):
        build_message("X", props, additional)


def test_input_vs_additional_collision() -> None:
    with pytest.raises(TypeError, match="multiple values"):
        build_message("X", dict(BASE), {"input": "clash"})


def test_no_template_passes_input_through() -> None:
    props = {k: v for k, v in BASE.items() if k != "input_template"}
    message = build_message("RAW", props, None)
    assert message["messages"][0]["content"] == "RAW"


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


def test_output_template() -> None:
    payload = {"choices": [{"message": {"content": "core"}}]}
    props = {
        "output_path": "$.choices[0].message.content",
        "output_template": "wrapped($output)",
    }
    assert extract_output(payload, props) == "wrapped(core)"


def test_jinja_placeholders_render() -> None:
    # Template substitution runs before Jinja; unknown Jinja names become empty.
    props = {**BASE, "input_template": "{{ input }} {{ missing }}"}
    message = build_message("X", props, {})
    assert message["messages"][0]["content"] == "X "


def test_recursive_substitution_resolves_chains() -> None:
    # Repetition resolves $a -> "$b" -> "done" before stopping.
    props = {**BASE, "input_template": "$a", "a": "$b", "b": "done"}
    message = build_message("X", props, {})
    assert message["messages"][0]["content"] == "done"


def test_output_template_jinja() -> None:
    payload = {"choices": [{"message": {"content": "core"}}]}
    props = {
        "output_path": "$.choices[0].message.content",
        "output_template": "wrapped({{ output }})",
    }
    assert extract_output(payload, props) == "wrapped(core)"


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
    message = build_message(
        "hello", {**BASE, "input_template": None} | {"input_template": "$input"}, {}
    )
    assert message["messages"] == [{"role": "user", "content": "hello"}]


def test_missing_output_path_yields_none() -> None:
    assert (
        extract_output({"nothing": True}, {"output_path": "$.choices[0].message.content"}) is None
    )
