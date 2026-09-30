"""What the plan shapes promise whoever reads a plan."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from blue_data_planner.types import (
    INPUT_ARITY,
    BlueDataPlannerError,
    ControlOperator,
    DataPipeline,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    PlanFormatError,
    UnknownFragmentError,
    UnknownNodeError,
    summarize_table,
)


def test_every_logical_operator_declares_how_many_tables_it_transforms() -> None:
    """A kind with no arity would let validation pass a plan it cannot check."""
    assert set(INPUT_ARITY) == set(LogicalOperator)
    assert INPUT_ARITY[LogicalOperator.SCAN] == 0
    assert INPUT_ARITY[LogicalOperator.JOIN] == 2
    assert INPUT_ARITY[LogicalOperator.UNION] == 2
    assert INPUT_ARITY[LogicalOperator.FILTER] == 1


def test_a_control_operator_is_not_a_logical_one() -> None:
    """The two vocabularies are separate, since a control node binds to no operator."""
    assert {member.value for member in ControlOperator} == {"REPLAN", "DISCOVER"}
    assert not {member.value for member in ControlOperator} & {
        member.value for member in LogicalOperator
    }


@pytest.mark.parametrize(
    ("kind", "is_operator"),
    [
        (LogicalOperator.SCAN, True),
        (LogicalOperator.JOIN, True),
        (LogicalOperator.RETURN, False),
        (ControlOperator.REPLAN, False),
        (ControlOperator.DISCOVER, False),
    ],
    ids=["scan", "join", "return", "replan", "discover"],
)
def test_only_a_kind_an_operator_runs_is_an_operator_node(
    kind: LogicalOperator | ControlOperator, is_operator: bool
) -> None:
    """RETURN is a logical operator but names no step, so no fragment ever covers it."""
    node = PipelineNode(
        id="n1", kind=kind, input_node_ids=[], attributes={}, required_output_columns=[]
    )

    assert node.is_operator_node is is_operator


def test_a_declared_column_names_a_column_and_the_type_its_values_coerce_to() -> None:
    """The type is declared rather than inferred, so `y` can be read as a boolean."""
    column = OutputColumn(name="is_remote", dtype="bool")

    assert (column.name, column.dtype) == ("is_remote", "bool")


def a_scan_filter_return_plan() -> LogicalPlan:
    """The shape TAG-Bench produces most: read a table, filter it, project two columns."""
    columns = [
        OutputColumn(name="title", dtype="str"),
        OutputColumn(name="company_id", dtype="int"),
    ]
    return LogicalPlan(
        id="3f2b9c14",
        parent_id=None,
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={
                    "instruction": "job postings",
                    "source": "/source/jobs/database/jobs/collection/public/entity/postings",
                },
                required_output_columns=[],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "the posting is a data scientist role"},
                required_output_columns=columns,
            ),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n2"],
                attributes={"columns": ["title", "company_id"]},
                required_output_columns=[],
            ),
        ],
        output_node_id="n3",
    )


def a_cover_fusing_the_scan_and_the_filter() -> PhysicalPlan:
    """One nl2sql fragment over n1 and n2, rooted at the filter."""
    return PhysicalPlan(
        id="b7c1d0e2",
        parent_id=None,
        logical_plan_id="3f2b9c14",
        fragments=[
            PhysicalPlanFragment(
                id="f1",
                logical_node_ids=["n1", "n2"],
                root_logical_node_id="n2",
                operator_id="blue.nl2sql",
                scan_sources={"n1": "/source/jobs/database/jobs/collection/public/entity/postings"},
                instruction="Find data scientist job postings, with their title and company id",
                input_data=[],
                required_output_columns=[
                    OutputColumn(name="title", dtype="str"),
                    OutputColumn(name="company_id", dtype="int"),
                ],
            )
        ],
    )


def a_cover_over_one_fragment_rooted_at_n1() -> PhysicalPlan:
    """One fragment covering n1 alone, the shape a cover of a single SCAN node takes."""
    return PhysicalPlan(
        id="b3a4c5d6",
        parent_id=None,
        logical_plan_id="3f2b9c14",
        fragments=[
            PhysicalPlanFragment(
                id="f1",
                logical_node_ids=["n1"],
                root_logical_node_id="n1",
                operator_id="blue.scan",
                scan_sources={"n1": "/source/jobs/database/jobs/collection/public/entity/postings"},
                instruction="Read job postings",
                input_data=[],
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            )
        ],
    )


def a_pipeline_holding_one_plan_and_its_cover() -> DataPipeline:
    """One version of each, both current, which is what a first pass produces."""
    plan = a_scan_filter_return_plan()
    cover = a_cover_fusing_the_scan_and_the_filter()
    return DataPipeline(
        logical_plans={plan.id: plan},
        physical_plans={cover.id: cover},
        current_logical_plan_id=plan.id,
        current_physical_plan_id=cover.id,
    )


def test_a_plan_answers_which_node_an_id_names_and_which_nodes_read_it() -> None:
    """The three query methods read nodes and input_node_ids and nothing else."""
    plan = a_scan_filter_return_plan()

    assert plan.node("n2").kind is LogicalOperator.FILTER
    assert [node.id for node in plan.consumers("n1")] == ["n2"]
    assert [node.id for node in plan.nodes_in_dependency_order()] == ["n1", "n2", "n3"]


def test_a_cover_answers_which_fragment_runs_a_node() -> None:
    """A physical plan describes no graph on its own; it is read by node id against the plan."""
    cover = a_cover_fusing_the_scan_and_the_filter()

    assert cover.fragment("f1").root_logical_node_id == "n2"
    assert cover.fragment_for_node("n1").id == "f1"


def test_a_node_rooting_no_fragment_of_the_cover_comes_back_as_nothing() -> None:
    """A node rooting no fragment is the ordinary case when a cover is being replaced."""
    cover = a_cover_over_one_fragment_rooted_at_n1()

    assert cover.fragment_for_root("n1") is not None
    assert cover.fragment_for_root("n9") is None


def test_a_pipeline_names_the_pair_that_produced_the_answer() -> None:
    """The two pointers resolve to the plan and the cover a reader should read first."""
    pipeline = a_pipeline_holding_one_plan_and_its_cover()

    assert pipeline.logical_plan.id == "3f2b9c14"
    assert pipeline.cover.logical_plan_id == "3f2b9c14"


@pytest.mark.parametrize(
    ("lookup", "expected_error"),
    [
        (lambda: a_scan_filter_return_plan().node("n9"), UnknownNodeError),
        (lambda: a_scan_filter_return_plan().consumers("n9"), UnknownNodeError),
        (lambda: a_cover_fusing_the_scan_and_the_filter().fragment("f9"), UnknownFragmentError),
        (
            lambda: a_cover_fusing_the_scan_and_the_filter().fragment_for_node("n3"),
            UnknownFragmentError,
        ),
        (lambda: DataPipeline({}, {}, None, None).logical_plan, BlueDataPlannerError),
        (lambda: DataPipeline({}, {}, None, None).cover, BlueDataPlannerError),
    ],
    ids=[
        "unknown-node-id",
        "consumers-of-an-unknown-node",
        "unknown-fragment-id",
        "a-node-no-fragment-covers",
        "no-logical-plan-is-current",
        "no-cover-is-current",
    ],
)
def test_a_lookup_for_something_the_plan_does_not_hold_raises(
    lookup: Callable[[], object], expected_error: type[Exception]
) -> None:
    """Every lookup raises and names what it could not find rather than returning None."""
    with pytest.raises(expected_error):
        lookup()


def test_a_cycle_has_no_dependency_order() -> None:
    """Ordering is the one traversal serialization and validation share, so it refuses a cycle."""
    plan = LogicalPlan(
        id="p",
        parent_id=None,
        nodes=[
            PipelineNode("n1", LogicalOperator.FILTER, ["n2"], {"instruction": "a"}, []),
            PipelineNode("n2", LogicalOperator.FILTER, ["n1"], {"instruction": "b"}, []),
        ],
        output_node_id=None,
    )

    with pytest.raises(BlueDataPlannerError, match="cycle"):
        plan.nodes_in_dependency_order()


def test_a_pipeline_survives_its_serialized_form_unchanged() -> None:
    """to_dict is how a plan leaves the process, and from_dict reads a recorded one back."""
    pipeline = a_pipeline_holding_one_plan_and_its_cover()

    assert DataPipeline.from_dict(pipeline.to_dict()) == pipeline


def test_a_world_knowledge_scans_null_source_survives_serialization() -> None:
    """A scan naming no source reads back as naming none, not as a missing attribute."""
    plan = LogicalPlan(
        id="p",
        parent_id=None,
        output_node_id=None,
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "read them", "source": None},
                required_output_columns=[],
            )
        ],
    )

    assert LogicalPlan.from_dict(plan.to_dict()) == plan


def test_serialization_writes_enum_members_and_column_types_as_their_values() -> None:
    """A plan leaves the process as JSON, so nothing in it may be a Python object."""
    payload = a_scan_filter_return_plan().to_dict()

    assert payload["nodes"][0]["kind"] == "SCAN"
    assert payload["nodes"][1]["required_output_columns"][0] == {"name": "title", "dtype": "str"}


_MALFORMED_PLAN_PAYLOADS: list[tuple[dict[str, object], str]] = [
    ({"id": "p", "parent_id": None, "output_node_id": None}, "nodes"),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SORT",
                    "input_node_ids": [],
                    "attributes": {"instruction": "a"},
                    "required_output_columns": [],
                }
            ],
        },
        "SORT",
    ),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SCAN",
                    "input_node_ids": [],
                    "attributes": {"columns": ["title"]},
                    "required_output_columns": [],
                }
            ],
        },
        "instruction",
    ),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "FILTER",
                    "input_node_ids": ["n9"],
                    "attributes": {"instruction": "a"},
                    "required_output_columns": [],
                }
            ],
        },
        "n9",
    ),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "FILTER",
                    "input_node_ids": [],
                    "attributes": {"instruction": "a"},
                    "required_output_columns": [{"name": "x", "dtype": "date"}],
                }
            ],
        },
        "date",
    ),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SCAN",
                    "input_node_ids": [],
                    "attributes": {"instruction": "a"},
                    "required_output_columns": [],
                }
            ],
        },
        "source",
    ),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SCAN",
                    "input_node_ids": [],
                    "attributes": {"instruction": "a", "source": 42},
                    "required_output_columns": [],
                }
            ],
        },
        "source",
    ),
    (
        {
            "id": "p",
            "parent_id": None,
            "output_node_id": None,
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SCAN",
                    "input_node_ids": [],
                    "attributes": {"instruction": "a", "source": None},
                    "required_output_columns": [{"name": "title", "dtype": "str"}],
                }
            ],
        },
        "declares no columns",
    ),
]


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    _MALFORMED_PLAN_PAYLOADS,
    ids=[
        "missing-key",
        "unknown-kind",
        "wrong-attribute",
        "input-naming-no-node",
        "unknown-dtype",
        "scan-missing-source",
        "scan-source-not-a-string",
        "scan-carrying-columns",
    ],
)
def test_reading_a_malformed_plan_raises_and_names_what_was_wrong(
    payload: dict[str, object], expected_message: str
) -> None:
    """from_dict is strict: nothing is repaired and nothing is ignored."""
    with pytest.raises(PlanFormatError, match=expected_message):
        LogicalPlan.from_dict(payload)


def test_a_summary_names_every_column_any_row_carries() -> None:
    """A row need not carry every key another row carries, so the columns are the union."""
    rows = [{"title": "SWE"}, {"company": "Acme", "title": "SRE"}]
    assert summarize_table(rows, 5).columns == ["company", "title"]


def test_a_summary_counts_the_whole_table_and_samples_the_front_of_it() -> None:
    """The count says how large the table is; the sample says what a row in it looks like."""
    summary = summarize_table([{"n": index} for index in range(40)], 3)
    assert summary.row_count == 40
    assert summary.sample_rows == [{"n": 0}, {"n": 1}, {"n": 2}]


@pytest.mark.parametrize("max_rows", [0, 5])
def test_an_empty_table_summarizes_to_no_columns_and_no_rows(max_rows: int) -> None:
    """An empty result is not a failure, so summarizing one is not either."""
    summary = summarize_table([], max_rows)
    assert (summary.row_count, summary.columns, summary.sample_rows) == (0, [], [])


def test_a_negative_row_cap_raises_rather_than_being_read_as_a_slice() -> None:
    """Python would read -1 as "all but the last row," which is not a cap."""
    with pytest.raises(ValueError, match="max_rows"):
        summarize_table([{"n": 1}], -1)
