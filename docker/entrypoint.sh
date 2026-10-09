#!/bin/sh
# v1.1.0 — wait for Postgres, migrate, then run whatever command was given
set -e

# The host and port come from DATABASE_URL, as Django reads them; without one
# (development) it is the compose service, db:5432.
echo "Waiting for Postgres..."
until python -c "
import os, socket, sys
from urllib.parse import urlsplit
url = urlsplit(os.environ.get('DATABASE_URL', 'postgres://db:5432'))
s = socket.socket()
s.settimeout(2)
try:
    s.connect((url.hostname or 'db', url.port or 5432))
except Exception:
    sys.exit(1)
" 2>/dev/null; do
  sleep 1
done

python manage.py migrate --noinput
exec "$@"
