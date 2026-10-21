#!/bin/bash
# Start the local server if it is not already up, then open the page.
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
URL="http://127.0.0.1:8099/"
STATUS="http://127.0.0.1:8099/api/status"

if ! command -v curl >/dev/null 2>&1; then
  echo "Bark Voice Notes needs curl, which is missing from this Mac."
  exit 1
fi

if curl -fsS --max-time 2 "$STATUS" >/dev/null 2>&1; then
  echo "Bark Voice Notes is already running."
  open "$URL"
  exit 0
fi

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "Run setup.sh first. The Python environment (.venv) is not here yet."
  exit 1
fi

mkdir -p "$ROOT/logs"
echo "Starting Bark Voice Notes…"
PYTHONUNBUFFERED=1 nohup "$ROOT/.venv/bin/python" "$ROOT/server.py" >>"$ROOT/logs/server.log" 2>&1 &

deadline=$((SECONDS + 20))
while [ "$SECONDS" -lt "$deadline" ]; do
  if curl -fsS --max-time 1 "$STATUS" >/dev/null 2>&1; then
    echo "Bark Voice Notes is ready."
    open "$URL"
    exit 0
  fi
  sleep 0.5
done

echo "Bark Voice Notes did not start within 20 seconds. Look at logs/server.log."
exit 1
