"""Where the TAG-Bench inputs and runs live, whatever the working directory is."""

from __future__ import annotations

from pathlib import Path

DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parents[4] / "data" / "tag_bench"
