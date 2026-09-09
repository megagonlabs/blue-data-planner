"""Hand-written, versioned prompt text, read by the components that write prompts (ADR-005)."""

from __future__ import annotations

from importlib.resources import files


def prompt_text(name: str) -> str:
    """Return the text of one prompt file, by its file name."""
    # __name__ holds this package's own name here, same as __package__, but is typed str
    # rather than str | None.
    return (files(__name__) / name).read_text(encoding="utf-8")
