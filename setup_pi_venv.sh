#!/usr/bin/env bash
set -euo pipefail

# Create and populate a Raspberry Pi-native virtual environment.
# Run this ON the Pi from the project root:
#   bash setup_pi_venv.sh

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3 first."
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "Using existing venv at $VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$PROJECT_DIR/requirements.txt"

python - <<'PY'
import requests, skyfield, reportlab, matplotlib
print("venv ok")
PY

echo
echo "Done. Use this interpreter in OpenCPN Launcher:"
echo "DISPLAY=:0 $VENV_DIR/bin/python $PROJECT_DIR/apps/maidenhead.py"
