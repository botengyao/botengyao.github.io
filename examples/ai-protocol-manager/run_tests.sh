#!/usr/bin/env bash
# Runs every config in configs/ on envoy-static against the mock providers.
#   ENVOY=/path/to/envoy-static ./run_tests.sh [TestClass[.test_name] ...]
set -euo pipefail
cd "$(dirname "$0")/test"
exec uv run --quiet --with pyyaml python -m unittest -v "${@:-test_examples}"
