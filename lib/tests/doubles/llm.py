"""A model client that answers from a queue, so a component's tests reach no provider."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from blue_data_planner.llm import Conversation, Turn
from blue_data_planner.types import LLMUsage


class ScriptedLLMClient:
    """Answers each structured call with the next scripted value, recording the conversation."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.conversations: list[list[tuple[str, str]]] = []

    def complete_structured(self, conversation: Conversation, response_model: type[Any]) -> Any:
        """Return the next scripted value, validating it against the schema it was asked for."""
        if not self.answers:
            raise AssertionError("the scripted client ran out of answers")
        answer = self.answers.pop(0)
        value = answer if isinstance(answer, BaseModel) else response_model.model_validate(answer)
        self.conversations.append([(turn.role, turn.content) for turn in conversation.turns])
        # Mirrors LLMClient.complete_structured, which appends the model's own turn: a
        # caller reading conversation.turns after the call sees the same shape either way.
        conversation.turns.append(Turn(role="assistant", content=value.model_dump_json()))
        conversation.usage.append(
            LLMUsage(
                model="scripted",
                prompt_tokens=0,
                completion_tokens=0,
                cost=0.0,
                latency_seconds=0.0,
            )
        )
        return value
