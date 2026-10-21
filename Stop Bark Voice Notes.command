#!/bin/bash
# Ask the server to quit. If that fails, stop only this app's server.py on port 8099.
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

if ! command -v curl >/dev/null 2>&1; then
  echo "Bark Voice Notes needs curl, which is missing from this Mac."
  exit 1
fi

echo "Stopping Bark Voice Notes…"
code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 -X POST "http://127.0.0.1:8099/api/quit" || true)"
if [ "$code" = "200" ]; then
  echo "Bark Voice Notes stopped."
  exit 0
fi

if ! command -v lsof >/dev/null 2>&1; then
  echo "Could not reach the server, and lsof is not available to find it."
  exit 1
fi

pids="$(lsof -tiTCP:8099 -sTCP:LISTEN 2>/dev/null || true)"
if [ -z "$pids" ]; then
  echo "Bark Voice Notes is not running."
  exit 0
fi

killed=0
for pid in $pids; do
  cmd="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
  case "$cmd" in
    *"$ROOT/server.py"*)
      kill "$pid" 2>/dev/null || true
      echo "Stopped process $pid."
      killed=1
      ;;
    *)
      echo "Left process $pid alone (not this app's server.py)."
      ;;
  esac
done

if [ "$killed" -eq 0 ]; then
  echo "Port 8099 is in use by another program. Not stopping it."
  exit 1
fi
exit 0
