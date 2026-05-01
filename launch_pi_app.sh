#!/usr/bin/env bash
set -euo pipefail

# Launch a pi-deck-tools app using the project-local Pi virtual environment.
#
# Usage:
#   bash launch_pi_app.sh maidenhead
#   bash launch_pi_app.sh hifiberry_volume
#   bash launch_pi_app.sh sun_moon
#
# Optional:
#   DISPLAY=:0 bash launch_pi_app.sh maidenhead

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
APPS_DIR="$PROJECT_DIR/apps"
DISPLAY_VALUE="${DISPLAY:-:0}"

if [ $# -ne 1 ]; then
  echo "Usage: bash launch_pi_app.sh <maidenhead|hifiberry_volume|sun_moon>"
  exit 1
fi

APP_NAME="$1"
APP_PATH="$APPS_DIR/${APP_NAME}.py"

if [ ! -x "$VENV_PY" ]; then
  echo "Missing venv python at: $VENV_PY"
  echo "Run: bash setup_pi_venv.sh"
  exit 1
fi

if [ ! -f "$APP_PATH" ]; then
  echo "Unknown app: $APP_NAME"
  echo "Valid options: maidenhead, hifiberry_volume, sun_moon"
  exit 1
fi

export DISPLAY="$DISPLAY_VALUE"
exec "$VENV_PY" "$APP_PATH"
