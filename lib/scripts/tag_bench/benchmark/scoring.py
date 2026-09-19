"""Reading the answer a run produced, and scoring it against the gold one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from blue_data_planner.types import DataBundle
from scripts.tag_bench.fetch_queries import AnswerValue

# TAG-Bench spells a boolean gold answer three ways ("Yes", "TRUE", "YES"), and none of them
# is a value stored in any database, so a run that answers the question correctly cannot
# reproduce the spelling. "Y" and "N" are left out: both are plausible column values.
BOOLEAN_SPELLINGS: dict[str, bool] = {"yes": True, "true": True, "no": False, "false": False}


@dataclass(frozen=True)
class ScoredAnswer:
    """What a run answered, whether it matches the gold answer, and why it states none."""

    prediction: Any | None
    correct: bool
    note: str | None = None


def states_an_answer(gold: AnswerValue) -> bool:
    """Whether the gold answer states something exact match can score."""
    return bool(gold.strip()) if isinstance(gold, str) else bool(gold)


def score_answer(answer: DataBundle | None, gold: AnswerValue) -> ScoredAnswer:
    """Score what a run answered against the gold answer, without raising on either."""
    if not states_an_answer(gold):
        return ScoredAnswer(
            prediction=None,
            correct=False,
            note="the query carries an empty gold answer, so exact match cannot score it",
        )
    if answer is None:
        return ScoredAnswer(prediction=None, correct=False, note="the run produced no answer")
    try:
        prediction = extract_prediction(answer)
    except ValueError as error:
        return ScoredAnswer(prediction=None, correct=False, note=str(error))
    return ScoredAnswer(prediction=prediction, correct=same_answer(prediction, gold))


def extract_prediction(answer: DataBundle) -> Any:
    """Return the answer a run produced, in the shape a gold answer is written in."""
    if len(answer) != 1:
        raise ValueError(f"the answer holds {len(answer)} tables, and one RETURN states one answer")
    rows = [list(row.values()) for row in answer[0]]
    if not rows:
        raise ValueError("the answer table holds no rows")
    # The values stay as the operators produced them, so the record shows what the run returned.
    # Comparison normalizes a number written as a string on either side.
    if len(rows) == 1 and len(rows[0]) == 1:
        return rows[0][0]
    if len(rows[0]) == 1:
        return [row[0] for row in rows]
    return rows


def same_answer(prediction: Any, gold: AnswerValue) -> bool:
    """Whether the prediction is the gold answer, whatever way either is written."""
    # Both sides flatten to fields, because a gold answer never states its own rows: a flat list
    # of several values does not say whether it is one row of many fields or many rows of one.
    # Comparing the field sequence still catches a wrong value, a wrong order, and a wrong count.
    predicted_fields = _fields(prediction)
    gold_fields = _fields(gold)
    return len(predicted_fields) == len(gold_fields) and all(
        _same_field(one, other) for one, other in zip(predicted_fields, gold_fields, strict=True)
    )


def _normalized(value: Any) -> Any:
    """Return a value that reads as a number as that number, so two spellings compare equal."""
    # A released list cell holds its numbers quoted (["115372", "115254"]), while the same
    # answer read off a source arrives as integers.
    if isinstance(value, str):
        text = value.strip()
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                continue
    return value


def _as_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return BOOLEAN_SPELLINGS.get(value.strip().lower())
    return None


def _same_field(prediction: Any, gold: Any) -> bool:
    predicted_boolean = _as_boolean(prediction)
    gold_boolean = _as_boolean(gold)
    # A boolean-like value matches only another one, which keeps True apart from a count of 1
    # despite bool subclassing int.
    if predicted_boolean is not None or gold_boolean is not None:
        return predicted_boolean == gold_boolean
    return bool(_normalized(prediction) == _normalized(gold))


def _fields(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return [value]
    collected: list[Any] = []
    for element in value:  # pyright: ignore[reportUnknownVariableType]
        # isinstance() narrows an Any-typed value to list[Unknown], not list[Any], so pyright
        # still calls the element partially unknown here; the isinstance() check makes it safe.
        if isinstance(element, list):
            collected.extend(element)  # pyright: ignore[reportUnknownArgumentType]
        else:
            collected.append(element)
    return collected
