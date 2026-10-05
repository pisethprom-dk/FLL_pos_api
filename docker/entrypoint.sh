#!/bin/sh
# v1.0.0 — wait for Postgres, migrate, then run whatever command was given
set -e

echo "Waiting for Postgres at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
until python -c "
import socket, os, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect((os.environ.get('POSTGRES_HOST', 'db'), int(os.environ.get('POSTGRES_PORT', 5432))))
except Exception:
    sys.exit(1)
" 2>/dev/null; do
  sleep 1
done

python manage.py migrate --noinput
exec "$@"
