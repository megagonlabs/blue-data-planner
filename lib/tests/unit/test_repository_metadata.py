"""Repository metadata that every package and the license must keep."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGES = ["lib", "platform/planner-cli", "platform/planner-mcp"]


@pytest.mark.parametrize("package", PACKAGES)
def test_package_declares_the_family_python_range(package: str) -> None:
    project = tomllib.loads((REPOSITORY / package / "pyproject.toml").read_text())["project"]
    assert project["requires-python"] == ">=3.12,<3.14"


def test_license_has_no_html_entities() -> None:
    license_text = (REPOSITORY / "LICENSE").read_text()
    assert "&#39;" not in license_text
    assert "'AS IS'" in license_text
