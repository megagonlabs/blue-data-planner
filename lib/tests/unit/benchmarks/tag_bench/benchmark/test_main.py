"""Tests for lib/tests/benchmarks/tag_bench/benchmark/__main__.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.benchmarks.tag_bench.benchmark.__main__ import (
    check_provider_key,
    parse_arguments,
    prepare_run_dir,
    record_path,
)


def test_the_run_command_defaults_to_the_dev_split_and_the_three_scorable_types() -> None:
    """The default selection is the one a debugging pass uses."""
    arguments = parse_arguments(["run"])

    assert arguments.split == "dev"
    assert arguments.query_type == ["Match", "Comparison", "Ranking"]
    assert arguments.workers == 4


@pytest.mark.parametrize(
    ("argv", "attribute", "expected"),
    [
        (["run", "--split", "test"], "split", "test"),
        (["run", "--query-type", "aggregation"], "query_type", ["Aggregation"]),
        (["run", "--query-id", "3", "--query-id", "9"], "query_id", [3, 9]),
        (["run", "--limit", "2"], "limit", 2),
        (["run", "--workers", "1"], "workers", 1),
        (["run", "--model", "openai/gpt-x"], "model", "openai/gpt-x"),
        (["run", "--lotus-max-rows", "100"], "lotus_max_rows", 100),
        (["report", "--run-name", "one"], "run_name", "one"),
    ],
)
def test_an_option_reaches_the_setting_it_names(
    argv: list[str], attribute: str, expected: object
) -> None:
    """Every option a run is steered with parses to the value the settings read."""
    assert getattr(parse_arguments(argv), attribute) == expected


@pytest.mark.parametrize(
    ("model", "variable", "raises"),
    [
        ("openai/gpt-5.6-luna", "OPENAI_API_KEY", True),
        ("ollama/llama3", "OPENAI_API_KEY", False),
    ],
)
def test_a_model_whose_provider_key_is_missing_is_refused_before_anything_is_spent(
    monkeypatch: pytest.MonkeyPatch, model: str, variable: str, raises: bool
) -> None:
    """A run over a provider with no key raises; a model no key is known for is left alone."""
    monkeypatch.delenv(variable, raising=False)

    if raises:
        with pytest.raises(ValueError, match=variable):
            check_provider_key(model)
    else:
        check_provider_key(model)


def test_a_named_run_gets_its_own_directory(tmp_path: Path) -> None:
    """The run directory is made under runs/, so nothing else has to create it."""
    run_dir = prepare_run_dir(tmp_path, "smoke")

    assert run_dir == tmp_path / "runs" / "smoke"
    assert run_dir.is_dir()


def test_a_run_directory_that_already_holds_records_is_refused(tmp_path: Path) -> None:
    """Two runs in one directory would leave a summary over both, so the second is refused."""
    run_dir = prepare_run_dir(tmp_path, "smoke")
    record_path(run_dir, 1).write_text(json.dumps({"query_id": 1}))

    with pytest.raises(ValueError, match="smoke"):
        prepare_run_dir(tmp_path, "smoke")
