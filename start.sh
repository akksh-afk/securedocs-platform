#!/bin/bash
# ===================================================================
#  SecureDocs - one command to run everything (macOS / Linux).
#
#      ./start.sh
#
#  Installs what is missing, prepares the database, builds the screens,
#  starts the text reader and the server, and opens your browser.
#
#  Requirements: Node.js and PostgreSQL installed and running.
# ===================================================================
set -e
cd "$(dirname "$0")"

echo
echo "  SecureDocs - starting up"
echo "  ========================"
echo

command -v node >/dev/null || { echo "  [X] Node.js is not installed - https://nodejs.org"; exit 1; }

if [ ! -f backend/.env ]; then
  echo "  [X] backend/.env is missing."
  echo "      cp backend/.env.example backend/.env  and fill in MASTER_KEY"
  exit 1
fi

echo "  [1/5] Installing components (first run takes a few minutes)..."
[ -d backend/node_modules ]  || (cd backend  && npm install --silent)
[ -d frontend/node_modules ] || (cd frontend && npm install --silent)

echo "  [2/5] Preparing the database..."
(cd backend && npm run --silent db:setup)

echo "  [3/5] Building the screens..."
(cd frontend && npm run --silent build)

echo "  [4/5] Starting the text reader..."
(cd backend && npm run worker > ../worker.log 2>&1 &)

echo "  [5/5] Starting the server..."
(cd backend && npm start > ../server.log 2>&1 &)

# Stop both when this script is interrupted.
trap 'echo; echo "  Stopping..."; pkill -f "node server.js" 2>/dev/null; pkill -f "ocr-worker.js" 2>/dev/null; exit 0' INT TERM

printf "  Waiting for the server"
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://localhost:5000/"; then break; fi
  printf "."
  sleep 0.5
done
echo

(command -v open >/dev/null && open http://localhost:5000) || \
(command -v xdg-open >/dev/null && xdg-open http://localhost:5000) || true

cat <<'BANNER'

  ===================================================================
    SecureDocs is running:   http://localhost:5000

    Sign in with
      Service number : DL-INS-1001
      Password       : Test@1234
      6-digit code   : cd backend && npm run code

    Logs: server.log and worker.log
    Press Ctrl+C to stop everything.
  ===================================================================

BANNER

# Hold the terminal open so Ctrl+C reaches the trap above.
while true; do sleep 3600; done
