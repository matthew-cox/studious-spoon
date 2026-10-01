#!/usr/bin/env bash
# Run pytest with the Docker runtime auto-detected, e.g. `scripts/test.sh admin -k csrf`.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/docker-env.sh
exec uv run pytest "$@"
