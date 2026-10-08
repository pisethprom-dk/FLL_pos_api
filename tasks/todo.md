<!-- v1.0.2 -->
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
