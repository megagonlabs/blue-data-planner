"""The provider route a litellm model string carries, and the model name left without it."""

from __future__ import annotations

import pytest

from blue_data_planner.llm.model_names import model_without_provider


@pytest.mark.parametrize(
    ("model", "without_provider"),
    [
        ("openai/gpt-6-luna", "gpt-6-luna"),
        ("gpt-6-luna", "gpt-6-luna"),
        ("fireworks_ai/accounts/fireworks/models/llama-v3", "accounts/fireworks/models/llama-v3"),
        ("accounts/fireworks/models/llama-v3", "accounts/fireworks/models/llama-v3"),
    ],
)
def test_only_a_leading_provider_name_is_removed_from_a_model_string(
    model: str, without_provider: str
) -> None:
    """A slash inside a model id names no route, so only a first segment litellm reads as a
    provider is removed."""
    assert model_without_provider(model) == without_provider
