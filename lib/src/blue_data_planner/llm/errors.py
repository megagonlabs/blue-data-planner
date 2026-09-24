from __future__ import annotations

from typing import Any


class LLMError(Exception):
    """Raised for LLM transport failures and rejected adapter configuration."""

    # Wrapping boundary: transport/provider exceptions and malformed responses are
    # wrapped here by the client; templating errors (an undefined prompt value, JSONPath
    # misses) remain raw because they occur before the provider boundary.

    def __init__(
        self, message: str, code: str | None = None, provider_error: Any | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.provider_error = provider_error
