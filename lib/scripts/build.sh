#!/usr/bin/env bash
# Build the wheel and the source distribution into lib/dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist
python -m build
