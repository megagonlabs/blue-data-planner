"""What reaches stdout, the channel the stdio transport speaks over."""

from __future__ import annotations

import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

# The server under test, with a planner that writes to stdout the three ways a dependency can:
# print, a log handler reading sys.stdout when it emits (LiteLLM's), and descriptor 1 directly.
NOISY_SERVER = """
import logging, os, sys

import blue_data_planner_mcp.config as config
from blue_data_planner_mcp.server import main
from tests.doubles.planner import FakePlanner, a_run_that_answered


class StdoutAtEmitHandler(logging.StreamHandler):
    def emit(self, record):
        self.stream = sys.stdout
        super().emit(record)


class NoisyPlanner(FakePlanner):
    def run(self, request):
        print("print noise")
        logger = logging.getLogger("noise")
        logger.addHandler(StdoutAtEmitHandler())
        logger.warning("log noise")
        os.write(1, b"descriptor noise\\n")
        return super().run(request)


config.get_planner = lambda: NoisyPlanner(a_run_that_answered())
main()
"""


async def test_a_planner_writing_to_stdout_leaves_the_protocol_intact() -> None:
    """Whatever the planner writes to stdout reaches stderr, and the tool call still answers."""
    parse_errors: list[Exception] = []

    async def record_parse_error(message: object) -> None:
        if isinstance(message, Exception):
            parse_errors.append(message)

    server = StdioServerParameters(command=sys.executable, args=["-c", NOISY_SERVER])
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, message_handler=record_parse_error
        ) as session:
            await session.initialize()
            result = await session.call_tool("query_data", {"request": "any question"})

    assert parse_errors == []
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert json.loads(content.text)["status"] == "ok"
