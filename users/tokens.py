# v1.1.0 — refresh-cookie handling, the absolute session cap, one session per user.
#
# Rotation gives a sliding 60-minute idle window: each refresh issues a new
# token with a fresh 60 minutes, so an active till never expires and an idle
# one dies after an hour. auth_time carries the original login forward through
# every rotation so a terminal left on overnight is dead by morning.
#
# One session per user: the latest sign-in wins. Each sign-in stamps a new id
# on the user and in the tokens (`sid`, copied onto every access token); a
# token whose sid is no longer the user's is refused, on refresh here and on
# every API call in users/authentication.py.
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

AUTH_TIME_CLAIM = "auth_time"
SESSION_CLAIM = "sid"

SESSION_ENDED = "session_ended"
SESSION_REPLACED = "session_replaced"


class SessionReplaced(TokenError):
    """The user has signed in somewhere else since this token was issued."""


def check_session(user, token):
    """Raise unless the token belongs to the user's current session."""
    sid = token.get(SESSION_CLAIM)
    if sid is not None and sid == str(user.current_session):
        return
    if user.current_session is None:
        # signed out, or an Admin reset the password
        raise TokenError("This session has ended. Sign in again.")
    raise SessionReplaced("You signed in on another device. Sign in again to use this one.")


def refusal_code(exc):
    """What a 401 tells the frontend: replaced by another sign-in, or ended."""
    return SESSION_REPLACED if isinstance(exc, SessionReplaced) else SESSION_ENDED


def issue_refresh(user):
    refresh = RefreshToken.for_user(user)
    refresh[AUTH_TIME_CLAIM] = int(time.time())
    refresh[SESSION_CLAIM] = user.start_session()
    refresh["role"] = user.role
    return refresh


def rotate_refresh(raw_token):
    """Validate an incoming refresh token and return (new_refresh, user_id).

    Raises TokenError if the token is invalid, expired, blacklisted, or if the
    absolute session cap has been passed; SessionReplaced if the user has
    signed in somewhere else since.
    """
    old = RefreshToken(raw_token)

    auth_time = old.get(AUTH_TIME_CLAIM)
    if auth_time is None:
        raise TokenError("Token is missing its login time.")

    cap_seconds = settings.AUTH_ABSOLUTE_SESSION_HOURS * 3600
    if int(time.time()) - int(auth_time) > cap_seconds:
        raise TokenError("This login has reached its maximum length. Sign in again.")

    user_id = old.get("user_id")

    # ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION mean the old token must
    # not work twice.
    if settings.SIMPLE_JWT.get("BLACKLIST_AFTER_ROTATION", False):
        try:
            old.blacklist()
        except AttributeError:  # blacklist app not installed
            pass

    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise TokenError("That account is no longer active.")
    check_session(user, old)

    new = RefreshToken.for_user(user)
    new[AUTH_TIME_CLAIM] = int(auth_time)   # carried forward, not reset
    new[SESSION_CLAIM] = old[SESSION_CLAIM]  # the same session, rotated
    new["role"] = user.role
    return new, user


def end_session(token):
    """Sign-out ends the session only while it is still the user's current one,
    so a replaced device signing out leaves the new sign-in alone."""
    sid = token.get(SESSION_CLAIM)
    if sid is None:
        return
    get_user_model().objects.filter(
        pk=token.get("user_id"), current_session=sid
    ).update(current_session=None)


def set_refresh_cookie(response, refresh):
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        str(refresh),
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path=settings.AUTH_COOKIE_PATH,
    )
    return response


def clear_refresh_cookie(response):
    response.delete_cookie(
        settings.AUTH_COOKIE_NAME,
        path=settings.AUTH_COOKIE_PATH,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )
    return response
