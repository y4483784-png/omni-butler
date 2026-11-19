#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
port_open() {
  python3 -c 'import socket; s=socket.create_connection(("127.0.0.1", 5173), 0.5); s.close()' >/dev/null 2>&1
}
if ! port_open; then
  (cd "$root/frontend" && npm run dev) &
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if port_open; then
      break
    fi
    sleep 0.5
  done
fi
cd "$root/desktop"
exec npx electron .
