"""The Blue operations a plan can be bound to, and what each call does with what it is handed."""

from __future__ import annotations

from blue_data_ops.operators.registry import versioned_registry

from blue_data_planner.operators.adapters.blue.operators import OPERATIONS


def test_every_operation_resolves_the_registry_id_it_names() -> None:
    """An id the registry does not carry has to fail while the suite is built, not mid-run."""
    registry = versioned_registry()

    for operation in OPERATIONS:
        assert registry.resolve(operation.registry_operator_id) is not None


def test_each_operation_binds_the_newest_card_the_registry_carries_for_it() -> None:
    """A newer card refuses more bad input in the operation's own validator, so it is the one
    bound."""
    registry = versioned_registry()

    behind: list[str] = []
    for operation in OPERATIONS:
        name, _, version = operation.registry_operator_id.partition("@")
        latest = registry.find_latest(name)
        if latest is not None and latest.version != version:
            behind.append(f"{operation.operator_id} binds {version}, and {latest.version} exists")

    assert not behind, behind
