"""Isolation every CLI test needs, since a query command exports a profile to the environment."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def a_restored_process_environment() -> Generator[None]:
    """Undo what one command exported, so one test's profile is not the next test's environment."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
