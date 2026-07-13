#!/usr/bin/env bash
# Linux/macOS launcher twin of launch.bat — starts the ColdRead GUI.
set -euo pipefail
cd "$(dirname "$0")"

# Prefer a built single-file bundle if present (no Python needed).
if [ -x "dist/ColdRead" ]; then
    exec "./dist/ColdRead"
fi

# Otherwise run the GUI module. Pick an interpreter: an explicit $PYTHON wins,
# then the project venv (where deps get installed), then a system python.
if [ -n "${PYTHON:-}" ]; then
    :
elif [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Error: no Python interpreter found. Install Python 3.10+ or set \$PYTHON." >&2
    exit 1
fi

# Fail early with an actionable message if the tool isn't installed, instead of
# dying on an ImportError deep inside the GUI (a fresh clone has no deps yet).
if ! "$PYTHON" -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('vo_format') and u.find_spec('customtkinter') else 1)" 2>/dev/null; then
    echo "Error: ColdRead's dependencies aren't installed for '$PYTHON'." >&2
    echo "Install the tool first:" >&2
    echo "    $PYTHON -m pip install -e ." >&2
    echo "or set up a virtualenv:" >&2
    echo "    python3 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi

exec "$PYTHON" -m vo_format.gui_main
