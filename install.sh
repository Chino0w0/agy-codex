#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
for python_cmd in python3 python; do
  if command -v "$python_cmd" >/dev/null 2>&1 &&
    "$python_cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    exec "$python_cmd" "$SCRIPT_DIR/install.py" "$@"
  fi
done
echo "Error: Python 3.10+ is required." >&2
exit 1
