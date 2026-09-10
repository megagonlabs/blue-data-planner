from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def installed_lotus() -> None:
    """Skip the whole tier where LOTUS is not installed, rather than failing to import."""
    pytest.importorskip("lotus", reason="run this tier in an environment carrying the lotus extra")


@pytest.fixture
def provider() -> None:
    """Skip a test that reaches a model where no key is configured."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")
