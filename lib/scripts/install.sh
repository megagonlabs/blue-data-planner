#!/usr/bin/env bash
# Install the library, and the blue-data and blue-data-ops checkouts beside this repository, in
# one editable environment.
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -e ../../blue-data/lib
pip install -e ../../blue-data-ops/lib
pip install -e '.[dev]'
