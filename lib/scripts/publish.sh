#!/usr/bin/env bash
# Upload what build.sh wrote into lib/dist/ to PyPI.
set -euo pipefail
cd "$(dirname "$0")/.."
twine upload dist/*
