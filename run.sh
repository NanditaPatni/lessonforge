#!/usr/bin/env bash
# macOS / Linux: ./run.sh   (first run installs everything and runs the doctor)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  python -m app.doctor
else
  . .venv/bin/activate
fi
echo "Open http://localhost:8000"
python -m uvicorn app.main:app --port 8000
