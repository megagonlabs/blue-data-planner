#!/usr/bin/env bash
# Install the CLI, the library it adapts, and the blue-data and blue-data-ops checkouts
# beside this repository, in one editable environment.
set -euo pipefail
cd "$(dirname "$0")/.."
bash ../../lib/scripts/install.sh
pip install -e '.[dev]'
