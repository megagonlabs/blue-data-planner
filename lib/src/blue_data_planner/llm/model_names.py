"""Reading the provider route out of a litellm model string."""

from __future__ import annotations


def model_without_provider(model: str) -> str:
    """Return a litellm model string with its leading provider route removed."""
    import litellm  # lazy, so the default test run can put its stub in place first

    provider, separator, name = model.partition("/")
    # Only a first segment litellm names as a provider is a route. A model id such as
    # "accounts/fireworks/models/llama-v3" contains slashes and names no provider.
    if separator and provider in litellm.provider_list:
        return name
    return model
