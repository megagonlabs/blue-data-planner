"""Which LiteLLM log records the server lets through."""

from __future__ import annotations

import logging

import pytest

from blue_data_planner_mcp.server import LITELLM_LOGGER_NAMES, quiet_litellm


@pytest.mark.parametrize("logger_name", LITELLM_LOGGER_NAMES)
def test_litellm_keeps_its_warnings_and_drops_its_info_records(
    logger_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """After quieting, a LiteLLM INFO record is dropped and a WARNING record still reaches a log."""
    quiet_litellm()
    logger = logging.getLogger(logger_name)

    with caplog.at_level(logging.DEBUG):
        logger.info("Wrapper: Completed Call")
        logger.warning("provider returned a retryable error")

    assert [record.getMessage() for record in caplog.records] == [
        "provider returned a retryable error"
    ]
