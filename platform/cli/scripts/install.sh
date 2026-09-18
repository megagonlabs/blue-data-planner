#!/usr/bin/env bash
# Install the CLI and the library it adapts, in one editable environment.
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -e ../../lib
pip install -e .
