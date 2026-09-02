"""Everything needed to assemble a router comes off the package root."""

from __future__ import annotations

import blue_data_planner


def test_a_router_can_be_assembled_without_an_internal_import() -> None:
    """Router, adapter, registry, and registry client are all reachable from the root."""
    router = blue_data_planner.OperatorRouter()
    operators = blue_data_planner.blue_operators(
        blue_data_planner.versioned_registry(),
        blue_data_planner.DataRegistryClient,  # pyright: ignore[reportArgumentType]
        # The class, not an instance: blue.nl2sql is the only operator that touches the
        # registry, and not until it is called. This test only builds them.
    )

    for operator in operators:
        router.register(operator)

    assert len(router.operators_for(blue_data_planner.LogicalOperator.AGGREGATE)) == 5


def test_the_routing_error_is_a_package_error() -> None:
    """A caller can catch the routing failure through the package's own base class."""
    assert issubclass(blue_data_planner.OperatorRoutingError, blue_data_planner.BlueDataOpsError)


def test_every_new_name_is_listed_in_all() -> None:
    """A name reachable but unlisted is not part of the released interface."""
    for name in (
        "OperatorRegistry",
        "OperatorRouter",
        "OperatorRoutingError",
        "blue_operators",
        "versioned_registry",
    ):
        assert name in blue_data_planner.__all__
