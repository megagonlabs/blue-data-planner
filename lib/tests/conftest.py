from __future__ import annotations

import sys
from pathlib import Path

# The `llm` extra is intentionally absent from the development dependencies; putting the stub
# directory on the path lets the client's lazy `import litellm` resolve offline.
sys.path.insert(0, str(Path(__file__).parent / "stubs"))

# Shared fixtures for the blue-data-planner test suite belong here; stage-specific
# fixtures stay beside their tests.
