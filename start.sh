#!/usr/bin/env sh
# Starts LiveSim on macOS / Linux. First run creates .venv and installs dependencies.
# Extra arguments are passed through, e.g.:  ./start.sh --port 8080 --history-days 7
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating a virtual environment in .venv ..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --disable-pip-version-check --upgrade pip
fi
echo "Checking dependencies ..."
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
exec .venv/bin/python -m livesim "$@"
