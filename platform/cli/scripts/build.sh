#!/usr/bin/env bash
# Build the wheel and the source distribution into platform/cli/dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist
python -m build
