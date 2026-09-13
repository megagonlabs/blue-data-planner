"""One parameter model per Blue operation, refusing every key it does not name."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Unknown keys are refused so a runner cannot reach a tuning parameter a declaration
# deliberately leaves out. Everything omitted keeps the operator's own default.
_STRICT = ConfigDict(extra="forbid")


class SelectParameters(BaseModel):
    """Filter records by one type-aware comparison against a key."""

    model_config = _STRICT

    operand_key: str = Field(description="The key to check in each record")
    operand: Literal["=", "!=", ">", ">=", "<", "<="] = Field(description="Comparison operator")
    # The operator declares this Any, which no strict schema can express. Naming the
    # types a plan actually compares against is what makes the schema fillable.
    operand_val: bool | int | float | str = Field(description="Value to compare with")


class ProjectParameters(BaseModel):
    """Keep a named subset of each record's keys, optionally renaming them."""

    model_config = _STRICT

    kept_keys: list[str] = Field(description="List of keys to keep in each record")
    key_mapping: dict[str, str] = Field(
        default_factory=dict[str, str],
        description="Dictionary mapping old key names to new key names",
    )


class SumParameters(BaseModel):
    """Add up one numeric key across every record."""

    model_config = _STRICT

    sum_key: str = Field(description="The key to sum the values for")
    output_key: str = Field(default="sum", description="The key name for the result sum")


class AvgParameters(BaseModel):
    """Average one numeric key across every record."""

    model_config = _STRICT

    avg_key: str = Field(description="The key to average the values for")
    output_key: str = Field(default="avg", description="The key name for the result average")


class MinParameters(BaseModel):
    """Return the smallest value of one key across every record."""

    model_config = _STRICT

    min_key: str = Field(description="The key to find the minimum value for")
    output_key: str = Field(default="min", description="The key name for the result min value")


class MaxParameters(BaseModel):
    """Return the largest value of one key across every record."""

    model_config = _STRICT

    max_key: str = Field(description="The key to find the maximum value for")
    output_key: str = Field(default="max", description="The key name for the result max value")


class ArgmaxParameters(BaseModel):
    """Return the record holding the largest value of one key."""

    model_config = _STRICT

    argmax_key: str = Field(description="The key to find the maximum value for")


class ArgminParameters(BaseModel):
    """Return the record holding the smallest value of one key."""

    model_config = _STRICT

    argmin_key: str = Field(description="The key to find the minimum value for")


class JoinParameters(BaseModel):
    """Join two tables on one key list per table."""

    model_config = _STRICT

    join_on: list[list[str]] = Field(description="List of join key lists for each data source")
    join_type: Literal["inner", "left", "right", "outer"] = Field(
        default="inner", description="Type of join"
    )
    keep_keys: Literal["left", "both"] = Field(
        default="left", description="'left' to keep left keys only, 'both' to keep both"
    )


class MatchOptionParameters(BaseModel):
    """How two records are compared, shared by the two set operations."""

    model_config = _STRICT

    match_option: Literal["key_match", "seq_match"] = Field(
        default="key_match",
        description=(
            "'key_match' compares field names and values; 'seq_match' compares by position"
        ),
    )


class SemanticFilterParameters(BaseModel):
    """Keep the records a model judges to satisfy a condition per field."""

    model_config = _STRICT

    filter_conditions: dict[str, str] = Field(
        description="Field name to the natural-language condition its value has to satisfy"
    )
    context: str = Field(
        default="", description="Optional domain knowledge to apply to every condition"
    )


class SemanticProjectParameters(BaseModel):
    """Keep and rename columns from a description of what the result should hold."""

    model_config = _STRICT

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
    extract_on_fields: list[str] = Field(
        default_factory=list[str],
        description="Fields to read; empty reads every field in the record",
    )
    type: str | None = Field(default=None, description="Expected type of the extracted value")


class SemanticExtractParameters(BaseModel):
    """Pull named entities out of each record's text into new columns."""

    model_config = _STRICT

    entities: list[EntityToExtract] = Field(
        description="The entities to extract, one per output column"
    )
    context: str = Field(
        default="", description="Optional domain knowledge to apply to every extraction"
    )


class TransformField(BaseModel):
    """One target column a semantic transform produces."""

    model_config = _STRICT

    source_fields: list[str] = Field(description="Input fields this column is computed from")
    description: str | None = Field(
        default=None, description="What the column should hold, in plain language"
    )
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

    description: str | None = Field(default=None, description="What the input field holds")
    type: str | None = Field(default=None, description="The input field's type")
    hints: str | None = Field(default=None, description="Extra guidance for reading the field")


class SemanticTransformParameters(BaseModel):
    """Compute new columns from existing ones with a model."""

    model_config = _STRICT

    output_desc: dict[str, TransformField] = Field(
        description="Target column name to how that column should be produced"
    )
    input_meta: dict[str, InputFieldMeta] = Field(
        default_factory=dict[str, InputFieldMeta],
        description="Input column name to what it holds, where the rows alone do not say",
    )


class SemanticJoinParameters(BaseModel):
    """Join two tables on a model's judgment of when two records match."""

    model_config = _STRICT

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


class NL2LLMParameters(BaseModel):
    """Answer a question from the model's own world knowledge."""

    model_config = _STRICT

    query: str = Field(description="The question to answer")
    context: str = Field(default="", description="Optional domain knowledge for the answer")
    attrs: list[QueryAttribute] = Field(
        default_factory=list[QueryAttribute],
        description="The fields each returned record should carry",
    )


class NL2SQLParameters(BaseModel):
    """Write one SQL query against a registered relational source and run it."""

    model_config = _STRICT

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
