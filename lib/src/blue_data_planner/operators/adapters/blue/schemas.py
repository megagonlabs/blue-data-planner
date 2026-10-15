"""One parameter model per Blue operation, refusing every key it does not name."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Unknown keys are refused so a runner cannot reach a tuning parameter a declaration
# deliberately leaves out. Everything omitted keeps the operator's own default.
_STRICT = ConfigDict(extra="forbid")


def _refuse_repeats(names: list[str], what: str) -> None:
    # Each list becomes a map before the operation sees it, so a repeated name would drop an
    # entry the model wrote.
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"each {what} may appear once; repeated: {', '.join(repeated)}")


def _by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item.pop("name"): item for item in items}


class BlueParameters(BaseModel):
    """What a model writes for one Blue operation, and the attributes the operation is run with."""

    model_config = _STRICT

    def operation_attributes(self) -> dict[str, Any]:
        """Return the attributes the operation is called with."""
        # exclude_none because the operators branch on a key's presence: semantic_extract
        # reads entity.get("description", <generated>) and semantic_transform tests
        # "type" in meta_info. A field dumped as None is present to both.
        return self.model_dump(exclude_none=True)


class SelectParameters(BlueParameters):
    """Filter records by one type-aware comparison against a key."""

    operand_key: str = Field(description="The key to check in each record")
    operand: Literal["=", "!=", ">", ">=", "<", "<="] = Field(description="Comparison operator")
    # The operator declares this Any, which no strict schema can express. Naming the
    # types a plan actually compares against is what makes the schema fillable.
    operand_val: bool | int | float | str = Field(description="Value to compare with")


class KeyRename(BaseModel):
    """One key a projection renames."""

    model_config = _STRICT

    old_key: str = Field(description="The key as the record carries it")
    new_key: str = Field(description="The name the key takes in the result")


class ProjectParameters(BlueParameters):
    """Keep a named subset of each record's keys, optionally renaming them."""

    kept_keys: list[str] = Field(description="List of keys to keep in each record")
    key_mapping: list[KeyRename] = Field(
        default_factory=list[KeyRename],
        description="Keys to rename, each from its old name to its new name",
    )

    @field_validator("key_mapping")
    @classmethod
    def each_key_is_renamed_once(cls, renames: list[KeyRename]) -> list[KeyRename]:
        """Refuse two renames of one key."""
        _refuse_repeats([rename.old_key for rename in renames], "old_key")
        return renames

    def operation_attributes(self) -> dict[str, Any]:
        """Return the attributes the operation is called with, the renames as one map."""
        attributes = super().operation_attributes()
        attributes["key_mapping"] = {rename.old_key: rename.new_key for rename in self.key_mapping}
        return attributes


class SumParameters(BlueParameters):
    """Add up one numeric key across every record."""

    sum_key: str = Field(description="The key to sum the values for")
    output_key: str = Field(default="sum", description="The key name for the result sum")


class AvgParameters(BlueParameters):
    """Average one numeric key across every record."""

    avg_key: str = Field(description="The key to average the values for")
    output_key: str = Field(default="avg", description="The key name for the result average")


class MinParameters(BlueParameters):
    """Return the smallest value of one key across every record."""

    min_key: str = Field(description="The key to find the minimum value for")
    output_key: str = Field(default="min", description="The key name for the result min value")


class MaxParameters(BlueParameters):
    """Return the largest value of one key across every record."""

    max_key: str = Field(description="The key to find the maximum value for")
    output_key: str = Field(default="max", description="The key name for the result max value")


class ArgmaxParameters(BlueParameters):
    """Return the record holding the largest value of one key."""

    argmax_key: str = Field(description="The key to find the maximum value for")


class ArgminParameters(BlueParameters):
    """Return the record holding the smallest value of one key."""

    argmin_key: str = Field(description="The key to find the minimum value for")


class JoinParameters(BlueParameters):
    """Join two tables on one key list per table."""

    join_on: list[list[str]] = Field(description="List of join key lists for each data source")
    join_type: Literal["inner", "left", "right", "outer"] = Field(
        default="inner", description="Type of join"
    )
    keep_keys: Literal["left", "both"] = Field(
        default="left", description="'left' to keep left keys only, 'both' to keep both"
    )


class MatchOptionParameters(BlueParameters):
    """How two records are compared, shared by the two set operations."""

    match_option: Literal["key_match", "seq_match"] = Field(
        default="key_match",
        description=(
            "'key_match' compares field names and values; 'seq_match' compares by position"
        ),
    )


class FieldCondition(BaseModel):
    """One field a semantic filter checks, and the condition its value has to satisfy."""

    model_config = _STRICT

    name: str = Field(description="The field to check")
    condition: str = Field(description="The condition its value has to satisfy, in plain language")


class SemanticFilterParameters(BlueParameters):
    """Keep the records a model judges to satisfy a condition per field."""

    filter_conditions: list[FieldCondition] = Field(
        description="One condition per field, each a value of that field has to satisfy"
    )
    context: str = Field(
        default="", description="Optional domain knowledge to apply to every condition"
    )

    @field_validator("filter_conditions")
    @classmethod
    def each_field_has_one_condition(cls, conditions: list[FieldCondition]) -> list[FieldCondition]:
        """Refuse two conditions on one field."""
        _refuse_repeats([condition.name for condition in conditions], "field name")
        return conditions

    def operation_attributes(self) -> dict[str, Any]:
        """Return the attributes the operation is called with, the conditions as one map."""
        attributes = super().operation_attributes()
        attributes["filter_conditions"] = {
            condition.name: condition.condition for condition in self.filter_conditions
        }
        return attributes


class SemanticProjectParameters(BlueParameters):
    """Keep and rename columns from a description of what the result should hold."""

    projection_instructions: str = Field(
        description="Which columns to keep and how to rename them, in plain language"
    )


class EntityToExtract(BaseModel):
    """One entity a semantic extraction pulls out of each record."""

    model_config = _STRICT

    name: str = Field(description="The output field name for this entity")
    description: str | None = Field(
        default=None, description="What to extract; omit to let the operator write one"
    )
    extract_on_fields: list[str] | None = Field(
        default=None,
        description="Fields to read; leave unset to read every field in the record",
    )
    type: str | None = Field(default=None, description="Expected type of the extracted value")


class SemanticExtractParameters(BlueParameters):
    """Pull named entities out of each record's text into new columns."""

    entities: list[EntityToExtract] = Field(
        description="The entities to extract, one per output column"
    )
    context: str = Field(
        default="", description="Optional domain knowledge to apply to every extraction"
    )


class TransformField(BaseModel):
    """One target column a semantic transform produces."""

    model_config = _STRICT

    name: str = Field(description="The target column's name")
    description: str = Field(description="What the column should hold, in plain language")
    source_fields: list[str] = Field(description="Input fields this column is computed from")
    type: str | None = Field(default=None, description="Expected type of the column's values")
    transformation_type: Literal[
        "field_mapping_without_value_change", "field_mapping_with_value_change"
    ] = Field(
        default="field_mapping_with_value_change",
        description="Whether the value is copied unchanged or computed from its sources",
    )
    required: bool | None = Field(
        default=None, description="Whether every record has to produce this column"
    )
    hints: str | None = Field(default=None, description="Extra guidance for computing the column")


class InputFieldMeta(BaseModel):
    """What an input column holds, where the rows alone do not say."""

    model_config = _STRICT

    name: str = Field(description="The input column's name")
    description: str | None = Field(default=None, description="What the input field holds")
    type: str | None = Field(default=None, description="The input field's type")
    hints: str | None = Field(default=None, description="Extra guidance for reading the field")


class SemanticTransformParameters(BlueParameters):
    """Compute new columns from existing ones with a model."""

    output_desc: list[TransformField] = Field(
        description="The target columns, each with how it should be produced"
    )
    input_meta: list[InputFieldMeta] = Field(
        default_factory=list[InputFieldMeta],
        description="Input columns whose contents the rows alone do not explain",
    )

    @field_validator("output_desc", "input_meta")
    @classmethod
    def each_column_is_named_once(
        cls, columns: list[TransformField] | list[InputFieldMeta]
    ) -> list[TransformField] | list[InputFieldMeta]:
        """Refuse two entries for one column."""
        _refuse_repeats([column.name for column in columns], "column name")
        return columns

    def operation_attributes(self) -> dict[str, Any]:
        """Return the attributes the operation is called with, each column list as one map."""
        attributes = super().operation_attributes()
        attributes["output_desc"] = _by_name(attributes["output_desc"])
        attributes["input_meta"] = _by_name(attributes["input_meta"])
        return attributes


class SemanticJoinParameters(BlueParameters):
    """Join two tables on a model's judgment of when two records match."""

    left_fields: list[str] = Field(description="Fields of the left table to compare on")
    right_fields: list[str] = Field(description="Fields of the right table to compare on")
    join_predicate: str = Field(
        default="", description="When two records should be joined, in plain language"
    )
    join_type: Literal["inner", "left", "right", "outer"] = Field(
        default="inner", description="Type of join"
    )
    keep_keys: Literal["left", "both"] = Field(
        default="left", description="'left' to keep left keys only, 'both' to keep both"
    )
    context: str = Field(default="", description="Optional domain knowledge for the comparison")


class QueryAttribute(BaseModel):
    """One output field a model-answered scan should produce."""

    model_config = _STRICT

    name: str = Field(description="The output field name")
    type: str | None = Field(default=None, description="Expected type of the field's values")


class NL2LLMParameters(BlueParameters):
    """Answer a question from the model's own world knowledge."""

    query: str = Field(description="The question to answer")
    context: str = Field(default="", description="Optional domain knowledge for the answer")
    attrs: list[QueryAttribute] = Field(
        default_factory=list[QueryAttribute],
        description="The fields each returned record should carry",
    )


class NL2SQLParameters(BlueParameters):
    """Write one SQL query against a registered relational source and run it."""

    question: str = Field(description="The question to translate into SQL")
    source: str = Field(
        description="Registered source name, from the fragment's scan_sources catalog path"
    )
    database: str = Field(description="Database name within the source")
    collection: str = Field(description="Collection or schema name within the database")
    protocol: Literal["sqlite", "mysql", "postgres"] = Field(
        description="The source's database protocol"
    )
    context: str = Field(default="", description="Optional domain knowledge for the translation")
    attr_names: list[str] = Field(
        default_factory=list[str],
        description="The field names each returned record should carry",
    )
