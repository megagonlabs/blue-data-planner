# blue-data-planner MCP server

**Not yet implemented.** `platform/mcp/` holds no source, and no `blue-data-planner-mcp` package is
published — see [`design/07-repo-organization.md`](../design/07-repo-organization.md) for where it
will sit.

Two tools are sketched in
[`design/09-api-interface-spec.md`](../design/09-api-interface-spec.md#3-mcp-server): `plan` writes
a plan and stops, `plan_and_execute` runs it. Both take a task in plain language and return the
run's JSON form. Treat that section as design, not as a working server.

The same two operations are available through:

- the [Python library](api-reference.md), via `DataPlanner.plan` and `DataPlanner.run`, or
- the [`blue-planner` CLI](cli.md), via `data query` with and without `--explain`.

This page gets the real tool reference once `platform/mcp/` exists.
