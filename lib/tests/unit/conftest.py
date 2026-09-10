from __future__ import annotations

import sys
from pathlib import Path

# The stubs stand in for optional dependencies the unit tier does not install, and they sit
# ahead of the real packages on purpose. Only this tier sees them: tests/lotus/ exists to
# reach an installed LOTUS, and a stub in front of it would defeat the point.
sys.path.insert(0, str(Path(__file__).parent.parent / "stubs"))
