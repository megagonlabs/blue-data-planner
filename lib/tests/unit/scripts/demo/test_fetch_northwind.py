"""Tests for lib/scripts/demo/fetch_northwind.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.demo.fetch_northwind import DEMO_FILE_NAMES, blue_data_revision, copy_demo_files

REVISION = "b2ca35214114156bdef50143d1ee7c05f42b6451"


def a_pyproject_pinning(tmp_path: Path, body: str) -> Path:
    """Write a pyproject.toml carrying body, returning its path."""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(body)
    return pyproject_path


def a_blue_data_checkout(tmp_path: Path, file_names: tuple[str, ...]) -> Path:
    """Stage a blue-data data/ directory holding file_names, returning it."""
    data_dir = tmp_path / "blue-data" / "data"
    data_dir.mkdir(parents=True)
    for file_name in file_names:
        (data_dir / file_name).write_text(f"contents of {file_name}")
    return data_dir


def test_blue_data_revision_returns_the_commit_the_uv_source_pins(tmp_path: Path) -> None:
    """The rev of the blue-data entry under [tool.uv.sources] is what comes back."""
    pyproject_path = a_pyproject_pinning(
        tmp_path,
        "[tool.uv.sources]\n"
        'blue-data-ops = { git = "ssh://example/ops.git", rev = "0000" }\n'
        f'blue-data = {{ git = "ssh://example/data.git", rev = "{REVISION}" }}\n',
    )
    assert blue_data_revision(pyproject_path) == REVISION


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (None, FileNotFoundError),
        ("[project]\nname = 'x'\n", ValueError),
        ('[tool.uv.sources]\nblue-data = { git = "ssh://example/data.git" }\n', ValueError),
    ],
    ids=["no pyproject", "no blue-data source", "no rev on the source"],
)
def test_blue_data_revision_refuses_a_pyproject_that_pins_no_revision(
    tmp_path: Path, body: str | None, error: type[Exception]
) -> None:
    """A pyproject that is absent, declares no blue-data source, or pins no rev raises."""
    pyproject_path = (
        tmp_path / "absent.toml" if body is None else a_pyproject_pinning(tmp_path, body)
    )
    with pytest.raises(error, match=str(pyproject_path.name)):
        blue_data_revision(pyproject_path)


def test_copy_demo_files_writes_both_seed_files_into_the_destination(tmp_path: Path) -> None:
    """Both demo files are copied, and their destination paths come back in order."""
    data_dir = a_blue_data_checkout(tmp_path, DEMO_FILE_NAMES)
    destination_dir = tmp_path / "data" / "demo"

    written = copy_demo_files(data_dir, destination_dir)

    assert written == [destination_dir / name for name in DEMO_FILE_NAMES]
    assert [path.read_text() for path in written] == [f"contents of {n}" for n in DEMO_FILE_NAMES]


def test_copy_demo_files_raises_naming_the_file_the_checkout_does_not_carry(
    tmp_path: Path,
) -> None:
    """A blue-data data/ missing one of the two files raises, naming the missing one."""
    data_dir = a_blue_data_checkout(tmp_path, DEMO_FILE_NAMES[:1])

    with pytest.raises(FileNotFoundError, match=DEMO_FILE_NAMES[1]):
        copy_demo_files(data_dir, tmp_path / "data" / "demo")
