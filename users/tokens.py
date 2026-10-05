# v1.0.0 — refresh-cookie handling and the absolute session cap.
#
# Rotation gives a sliding 60-minute idle window: each refresh issues a new
# token with a fresh 60 minutes, so an active till never expires and an idle
# one dies after an hour. auth_time carries the original login forward through
# every rotation so a terminal left on overnight is dead by morning.
import time

from django.conf import settings
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

AUTH_TIME_CLAIM = "auth_time"


def issue_refresh(user):
    refresh = RefreshToken.for_user(user)
    refresh[AUTH_TIME_CLAIM] = int(time.time())
    refresh["role"] = user.role
    return refresh


def rotate_refresh(raw_token):
    """Validate an incoming refresh token and return (new_refresh, user_id).

    Raises TokenError if the token is invalid, expired, blacklisted, or if the
    absolute session cap has been passed.
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

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise TokenError("That account is no longer active.")

    new = RefreshToken.for_user(user)
    new[AUTH_TIME_CLAIM] = int(auth_time)   # carried forward, not reset
    new["role"] = user.role
    return new, user


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
