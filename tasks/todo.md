<!-- v1.2.0 -->
# One session per user — latest login wins

**Asked (2026-10-08):** a user may be signed in in one place only. A new
sign-in keeps the new session and ends the previous one.

## Approach

Each user carries one "current session" id. Login makes a new one, stores it
on the user and writes it into the tokens as a `sid` claim. A token whose
`sid` is not the user's current one is refused, so the older device is out.

- **Access token** (every API call): `JWTAuthentication` already reads the
  user row on each request; a small subclass compares `sid` against it. No
  extra query. The old device is cut off on its next click, not 15 min later.
- **Refresh cookie**: `rotate_refresh()` makes the same check and carries the
  `sid` forward on rotation, as it already does with `auth_time`.
- **401 body** carries `code: "session_replaced"` and the message "You signed
  in on another device.", so the frontend can tell it apart from an expiry
  (`session_ended`).
- **Tabs in one browser** share the cookie, so they are one session and do
  not knock each other out. Two browsers on one PC are two sessions.

### What the old device sees
Next click → 401 → the interceptor tries one refresh → also 401 → sign-in
page. A held sale is saved on the server and survives; an unsaved cart on
the old screen is lost.

### Consequences
- **At deploy:** tokens issued before the change have no `sid`, so everyone
  signed in at that moment signs in once more.
- **Django admin** (`/admin/`) runs on Django's own sessions and is not
  covered.

## Todo

- [x] `users/models.py`: `User.current_session` (UUID, null, not editable) + migration
- [x] `users/tokens.py`: `issue_refresh()` makes a new session id and stamps `sid`;
      `rotate_refresh()` refuses a replaced one and carries `sid` forward
- [x] `users/authentication.py` (new): JWT authentication that refuses a
      replaced `sid` with code `session_replaced`; set as the default in
      `config/settings.py`; keep the schema at 0 errors and 0 warnings
- [x] `users/views.py`: logout clears `current_session` only when it is the
      current session (an old device signing out must not end the new one)
- [x] Admin "reset password" also ends the user's session
- [x] `users/tests.py`: second login ends the first one's access token and
      refresh cookie; the second keeps working; an old device's logout leaves
      the new session alone; a token with no `sid` is refused
- [x] Run the full suite (248 tests + new ones)
- [x] CLAUDE.md: Auth section + test count
- [ ] Frontend (separate step, `../pos_frontend`): the sign-in page says
      "signed in on another device" when the refresh fails with `session_replaced`

## Owner's decisions (2026-10-08)

1. Everyone — Admins and Sellers alike.
2. Cut off on the old device's next click (the access token is checked too).
3. The old screen says "signed in on another device" — frontend follow-up.
4. An Admin's "reset password" signs that user out.

## Review

Backend done; 254 tests pass (248 + 6), schema still 0 errors / 0 warnings.

- **`users/models.py` v1.1.0** — `current_session` UUID and `start_session()`;
  migration `0002_user_current_session`.
- **`users/tokens.py` v1.1.0** — `issue_refresh()` starts a new session and
  stamps `sid`; `rotate_refresh()` runs `check_session()` and carries `sid`
  forward; `end_session()` for sign-out; `refusal_code()` maps a refusal to
  `session_replaced` / `session_ended`.
- **`users/authentication.py` v1.0.0 (new)** — `SessionJWTAuthentication`,
  the default in `config/settings.py` v1.0.9. 401 body `{detail, code}`.
- **`users/schema.py` v1.0.0 (new)**, **`users/apps.py` v1.1.0** — keeps the
  subclass under the same `jwtAuth` scheme in the OpenAPI schema.
- **`users/views.py` v1.1.0** — refresh 401 rendered through
  `SessionEndedSerializer` (`users/serializers.py` v1.0.3) with its code;
  logout ends the session when it is the current one; reset-password ends it.
- **`users/tests.py` v1.0.4** — 6 tests: replaced at next click (access and
  refresh), own refreshes keep going, old device's sign-out leaves the new one,
  sign-out kills the access token, pre-change token refused, reset-password
  signs out.

**Change from the plan:** a token is refused with `session_ended` ("This
session has ended") when the user has no session at all — signed out, or
password reset — and `session_replaced` only when a newer sign-in exists, so
a reset-password user is not told they signed in elsewhere.

**Dev database:** migration applied; the web container was restarted (the
autoreloader had stopped on a half-saved file).

**Still to do:** the frontend message. The refresh 401 is now typed
`SessionEnded` in the schema, so the API client needs regenerating there.

---

# Deploy config for EC2

**Asked (2026-10-09):** settings from env vars (`SECRET_KEY`, `DEBUG=0`,
`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, database from `DATABASE_URL`),
`STATIC_ROOT` / `MEDIA_ROOT`, `SECURE_PROXY_SSL_HEADER` because nginx
terminates TLS, gunicorn and psycopg in requirements.

## Already in place — no change

- `SECRET_KEY`, `DEBUG` (off by default; `DEBUG=0` reads as off) and
  `ALLOWED_HOSTS` come from env — `config/settings.py:9-19`.
- `STATIC_ROOT = BASE_DIR / "staticfiles"`, `MEDIA_ROOT = BASE_DIR / "media"`
  — `config/settings.py:101-103`.
- `gunicorn==23.0.0`, `psycopg[binary]==3.2.4` — `requirements.txt`.

## Approach

Production refuses to start without what it must have; development keeps
working with no change to anyone's `.env`.

- **`SECRET_KEY`** required when `DEBUG` is off. Today it falls back to
  `"insecure-dev-key"` silently — and that key signs every JWT.
- **`DATABASE_URL`** via `env.db()` (django-environ, already installed).
  Required when `DEBUG` is off; in development it defaults to
  `postgres://pos:pos@db:5432/pos`, what docker-compose already runs.
  `POSTGRES_*` stay in `.env` only for the Postgres container itself.
- **`CSRF_TRUSTED_ORIGINS`** from env, e.g. `https://pos.example.com`. The
  API uses JWT and needs no CSRF; the Django admin's login form does.
- **`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`**. Safe
  only because nginx sets that header itself (`proxy_set_header
  X-Forwarded-Proto $scheme;`), overwriting anything a client sends.
- **Entrypoint** waits for the host and port in `DATABASE_URL`, not
  `POSTGRES_HOST` (unset on RDS, so it would wait for `db` forever).

## Owner's decisions (2026-10-09)

1. Postgres runs in a container on the same EC2.
2. Settings **and** the production run files.
3. The Django admin's cookies are Secure when `DEBUG` is off.
4. nginx on the host, with certbot.
5. `/api/docs/` and `/api/schema/` hidden in production (nginx 404); still
   there locally to generate the Angular client.

## Production layout (one EC2, Ubuntu)

```
internet ─443─▶ nginx (on the host, certbot TLS)
                 ├─ /            Angular build   /var/www/pos
                 ├─ /static/     collectstatic   /srv/pos/static
                 ├─ /media/      uploads         /srv/pos/media
                 └─ /api/ /admin/ ─▶ 127.0.0.1:8000 gunicorn (web container)
                                                   └─▶ db container (not published)
```

- **nginx on the host**, not in a container: `certbot --nginx` then gets and
  renews the certificate by itself, and adds the HTTPS block and the
  http→https redirect. The shipped site file is plain HTTP for that reason.
- **Production compose** (`docker-compose.prod.yml`, stand-alone so nothing
  from the dev file leaks in): `restart: unless-stopped`; Postgres with no
  published port; web runs `collectstatic` then gunicorn
  (`GUNICORN_WORKERS`, default 3), published on `127.0.0.1:8000` only;
  static and media bind-mounted to `/srv/pos/…` so host nginx can read
  them (a home directory is 750 on Ubuntu, nginx can't); log rotation
  (10 MB × 3) so container logs cannot fill the disk.
- **nginx sets `X-Forwarded-Proto $scheme`** itself, which is what makes
  `SECURE_PROXY_SSL_HEADER` safe; `client_max_body_size 10m` for images and
  the stock import.

## Todo

- [x] `config/settings.py`: `SECRET_KEY` and `DATABASE_URL` required when
      `DEBUG` is off; `CSRF_TRUSTED_ORIGINS`; `SECURE_PROXY_SSL_HEADER`;
      `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` when `DEBUG` is off
- [x] `docker/entrypoint.sh`: wait on `DATABASE_URL`'s host and port
- [x] `.env.example`: `DATABASE_URL`, `CSRF_TRUSTED_ORIGINS`, production
      values shown in comments
- [x] `docker-compose.prod.yml` (new)
- [x] `deploy/nginx/pos.conf` (new)
- [x] `deploy/README.md` (new): the EC2 steps, first deploy and updates
- [x] Run the suite; `manage.py check --deploy` with production-like env;
      bring the production compose up locally and check it serves
- [x] CLAUDE.md "Running it": a short production paragraph

## Review

255 tests pass. Nothing changes for development: with `DEBUG` on, the
database and key fall back to what docker-compose runs.

- **`config/settings.py` v1.1.0** — `DEBUG` first; `SECRET_KEY` and
  `DATABASE_URL` (`env.db()`) required when it is off; `CSRF_TRUSTED_ORIGINS`;
  `SECURE_PROXY_SSL_HEADER`; admin cookies Secure when `DEBUG` is off. The
  `POSTGRES_*` settings are gone from Django; they stay for the container.
- **`docker/entrypoint.sh` v1.1.0** — waits on `DATABASE_URL`'s host and port.
- **`.env.example` v1.1.0** — `DATABASE_URL`, `CSRF_TRUSTED_ORIGINS`,
  `GUNICORN_WORKERS`, production values in comments.
- **`docker-compose.prod.yml` v1.0.0 (new)**, **`deploy/nginx/pos.conf`
  v1.0.0 (new)**, **`deploy/README.md` v1.0.0 (new)** — as planned.
- **`.dockerignore` v1.0.1** — not in the plan: `backups/` (real shop data)
  and `.git/` kept out of the image.
- **CLAUDE.md** — a "Production (one EC2)" paragraph.

**Checked locally:**
- `DEBUG=0` without `SECRET_KEY`, or without `DATABASE_URL`, refuses to
  start, naming the missing variable.
- `check --deploy`: only W004 (no HSTS) and W008 (no SSL redirect in
  Django). certbot's nginx redirect covers W008; HSTS is left until HTTPS
  is known to work, since browsers remember it.
- The production compose, run under its own project name: migrations,
  163 static files collected, gunicorn up, 0 restarts; Postgres not
  published; web on 127.0.0.1 only.
- Over HTTP with `X-Forwarded-Proto: https`: the refresh cookie and the
  admin's `csrftoken` are Secure; a foreign Host gets 400; a 404 is plain;
  an admin sign-in POST gets 302 into `/admin/`, and the same POST from a
  foreign origin gets 403.
- `pos.conf` in an nginx container in front of it: `nginx -t` passes; `/`,
  `/sell` and other screens serve Angular; brand, `/admin/` and the admin's
  static files pass through; `/api/docs/` and `/api/schema/` are 404.
- Everything the check created was removed again.

Not done: HTTPS itself (certbot runs on the server), and anything on EC2.
