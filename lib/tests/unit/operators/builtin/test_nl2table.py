"""What builtin.nl2table reads, what it writes, and what it does when a program will not run."""

from __future__ import annotations

import litellm  # the tests/stubs deterministic fake (conftest inserts the stub path)
import pytest

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient, LLMError
from blue_data_planner.operators.builtin.nl2table import (
    MAX_PROGRAMS,
    nl2table_operator,
    program_prompt,
    read_tables,
    retry_prompt,
)
from blue_data_planner.operators.builtin.schemas import Nl2TableParameters
from blue_data_planner.types import OutputColumn, RecoverableOperatorError

STORES = "/source/tag_bench/database/retail/collection/public/entity/stores"
MODEL_PROPS = {"service_prefix": "openai", "openai.model": "gpt-6-luna"}


@pytest.fixture(autouse=True)
def _reset_fake() -> None:  # pyright: ignore[reportUnusedFunction] — autouse
    litellm.calls.clear()
    litellm.fail_next.clear()
    litellm.replies.clear()


def _reply(code: str) -> str:
    import json

    return json.dumps({"code": code})


def _run(
    registry: DataRegistry, parameters: dict[str, object], inputs: list[list[dict[str, object]]]
):
    operator = nl2table_operator(registry, LLMClient("test", MODEL_PROPS))
    return operator.call(inputs, parameters, [OutputColumn(name="city", dtype="str")])


def test_each_entity_is_read_under_the_name_its_catalog_path_ends_in(
    sqlite_registry: DataRegistry,
) -> None:
    """A program refers to a scanned entity by its entity name, and to the bundle by position."""
    parameters = Nl2TableParameters.model_validate(
        {"instruction": "anything", "scan_sources": [{"scan_source": STORES}]}
    )

    tables, _ = read_tables(sqlite_registry, parameters, [[{"city": "Reno"}]])

    assert sorted(tables) == ["input_0", "stores"]
    assert tables["stores"] == [
        {"store_id": 1, "city": "Reno"},
        {"store_id": 2, "city": "Oakland"},
    ]
    assert tables["input_0"] == [{"city": "Reno"}]


def test_a_bind_join_narrows_the_entity_to_the_values_an_input_carries(
    sqlite_registry: DataRegistry,
) -> None:
    """The input the index names supplies the values, and the report says how they travelled."""
    parameters = Nl2TableParameters.model_validate(
        {
            "instruction": "anything",
            "scan_sources": [
                {
                    "scan_source": STORES,
                    "bound_values": {
                        "input_index": 1,
                        "input_column": "town",
                        "attribute": "city",
                    },
                }
            ],
        }
    )

    tables, reports = read_tables(
        sqlite_registry, parameters, [[{"unused": 1}], [{"town": "Oakland"}]]
    )

    assert tables["stores"] == [{"store_id": 2, "city": "Oakland"}]
    assert reports[0]["bind_join"] == "in_list"


def test_two_entities_sharing_a_name_are_told_apart(sqlite_registry: DataRegistry) -> None:
    """Reading one entity twice gives the program two names rather than one table."""
    parameters = Nl2TableParameters.model_validate(
        {
            "instruction": "anything",
            "scan_sources": [{"scan_source": STORES}, {"scan_source": STORES}],
        }
    )

    tables, _ = read_tables(sqlite_registry, parameters, [])

    assert sorted(tables) == ["stores", "stores_2"]


def test_an_input_index_naming_no_table_refuses_the_call(sqlite_registry: DataRegistry) -> None:
    """The parameters bound a table the fragment never delivered."""
    parameters = Nl2TableParameters.model_validate(
        {
            "instruction": "anything",
            "scan_sources": [
                {
                    "scan_source": STORES,
                    "bound_values": {
                        "input_index": 3,
                        "input_column": "city",
                        "attribute": "city",
                    },
                }
            ],
        }
    )

    with pytest.raises(RecoverableOperatorError, match="input_index"):
        read_tables(sqlite_registry, parameters, [[{"city": "Reno"}]])


def test_the_prompt_shows_each_table_by_name_with_its_columns_and_some_rows() -> None:
    """A program is written against the tables as they are, not against a catalog record."""
    tables = {"stores": [{"store_id": 1, "city": "Reno"}, {"store_id": 2, "city": "Oakland"}]}

    prompt = program_prompt("count the stores", tables, [])

    assert "count the stores" in prompt
    assert "stores" in prompt
    assert "store_id" in prompt
    assert "Reno" in prompt
    assert "2 rows" in prompt


def test_the_prompt_names_the_columns_the_result_has_to_carry() -> None:
    """The declared columns reach the program rather than being paraphrased into the instruction."""
    prompt = program_prompt(
        "count the stores",
        {},
        [OutputColumn(name="city", dtype="str"), OutputColumn(name="n", dtype="int")],
    )

    assert "city" in prompt
    assert "str" in prompt
    assert "n" in prompt
    assert "int" in prompt


def test_the_prompt_renders_row_data_containing_template_syntax_literally() -> None:
    """A cell holding template-looking text is opaque data, never evaluated as a template."""
    tables = {"stores": [{"note": "{{ 7 * 6 }}"}]}

    prompt = program_prompt("x", tables, [])

    assert "{{ 7 * 6 }}" in prompt
    assert "42" not in prompt


def test_the_prompt_renders_a_dollar_sign_in_row_data_without_raising() -> None:
    """A currency value or any other literal '$' in a cell is not a placeholder reference."""
    tables = {"stores": [{"price": "$5.00"}]}

    prompt = program_prompt("x", tables, [])

    assert "$5.00" in prompt


def test_a_retry_prompt_carries_the_program_and_what_it_did() -> None:
    """The next program is written against the failure rather than against the request alone."""
    prompt = retry_prompt("result = stores[9]", "IndexError: list index out of range")

    assert "result = stores[9]" in prompt
    assert "IndexError" in prompt


def test_a_program_written_from_the_instruction_produces_the_calls_table(
    sqlite_registry: DataRegistry,
) -> None:
    """One model call, one program, one table: the ordinary path end to end."""
    litellm.replies.append(_reply("result = [{'city': row['city']} for row in stores]"))

    output = _run(
        sqlite_registry,
        {"instruction": "list the cities", "scan_sources": [{"scan_source": STORES}]},
        [],
    )

    assert output.data == [[{"city": "Reno"}, {"city": "Oakland"}]]


def test_a_program_that_will_not_run_is_written_again_with_its_failure(
    sqlite_registry: DataRegistry,
) -> None:
    """The second program is written against what the first one did."""
    litellm.replies.extend([_reply("result = stores[9]"), _reply("result = [{'city': 'Reno'}]")])

    output = _run(
        sqlite_registry,
        {"instruction": "list the cities", "scan_sources": [{"scan_source": STORES}]},
        [],
    )

    assert output.data == [[{"city": "Reno"}]]
    assert "IndexError" in litellm.calls[-1]["messages"][-1]["content"]


def test_the_call_ends_recoverably_once_every_program_has_been_written(
    sqlite_registry: DataRegistry,
) -> None:
    """Three programs is the bound, and the failure names the last one's undoing."""
    litellm.replies.extend([_reply("result = stores[9]")] * MAX_PROGRAMS)

    with pytest.raises(RecoverableOperatorError, match="IndexError"):
        _run(
            sqlite_registry,
            {"instruction": "list the cities", "scan_sources": [{"scan_source": STORES}]},
            [],
        )

    assert len(litellm.calls) == MAX_PROGRAMS


def test_a_read_that_fails_ends_the_call_before_a_program_is_written(
    sqlite_registry: DataRegistry,
) -> None:
    """No program was at fault, so none is written and the repairs outside get the parameters."""
    with pytest.raises(RecoverableOperatorError):
        _run(
            sqlite_registry,
            {"instruction": "x", "scan_sources": [{"scan_source": "/nonsense"}]},
            [],
        )

    assert litellm.calls == []


def test_a_provider_failure_is_not_answered_with_another_program(
    sqlite_registry: DataRegistry,
) -> None:
    """LLMError reaches the run loop, which ends the request as llm_failed (ADR-020)."""
    litellm.fail_next.extend([RuntimeError("provider down")] * 3)  # past the client's own retries

    with pytest.raises(LLMError):
        _run(
            sqlite_registry,
            {"instruction": "list the cities", "scan_sources": [{"scan_source": STORES}]},
            [],
        )


def test_the_report_names_every_program_written_every_query_run_and_what_it_cost(
    sqlite_registry: DataRegistry,
) -> None:
    """A finished call can be read: the programs, the scans, and the model calls."""
    litellm.replies.extend([_reply("result = stores[9]"), _reply("result = [{'city': 'Reno'}]")])

    output = _run(
        sqlite_registry,
        {"instruction": "list the cities", "scan_sources": [{"scan_source": STORES}]},
        [],
    )

    assert output.report is not None
    assert len(output.report["programs"]) == 2
    assert output.report["programs"][0]["error"] is not None
    assert output.report["programs"][1]["error"] is None
    assert output.report["scans"][0]["table"] == "stores"
    assert len(output.report["usage"]) == 2
