"""What builtin.nl2table reads, what it writes, and what it does when a program will not run."""

from __future__ import annotations

import pytest

from blue_data_planner.data import DataRegistry
from blue_data_planner.operators.builtin.nl2table import program_prompt, read_tables, retry_prompt
from blue_data_planner.operators.builtin.schemas import Nl2TableParameters
from blue_data_planner.types import OutputColumn, RecoverableOperatorError

STORES = "/source/tag_bench/database/retail/collection/public/entity/stores"


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


def test_a_retry_prompt_carries_the_program_and_what_it_did() -> None:
    """The next program is written against the failure rather than against the request alone."""
    prompt = retry_prompt("result = stores[9]", "IndexError: list index out of range")

    assert "result = stores[9]" in prompt
    assert "IndexError" in prompt
