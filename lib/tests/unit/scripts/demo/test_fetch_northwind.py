"""Tests for lib/scripts/demo/fetch_northwind.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.scripts.demo.script_loader import load_demo_script

fetch_northwind = load_demo_script("fetch_northwind")
DEMO_FILE_NAMES = fetch_northwind.DEMO_FILE_NAMES
copy_demo_files = fetch_northwind.copy_demo_files


def a_blue_data_checkout(tmp_path: Path, file_names: tuple[str, ...]) -> Path:
    """Stage a blue-data data/ directory holding file_names, returning it."""
    data_dir = tmp_path / "blue-data" / "data"
    data_dir.mkdir(parents=True)
    for file_name in file_names:
        (data_dir / file_name).write_text(f"contents of {file_name}")
    return data_dir


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
