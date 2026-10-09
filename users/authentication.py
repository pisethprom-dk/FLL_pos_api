# v1.0.0 — JWT on every API call, refused once the user has signed in elsewhere.
#
# The base class already reads the user row for each request, so checking the
# session costs no extra query, and a replaced device is out on its next click
# rather than when its 15-minute access token runs out.
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError

from users.tokens import check_session, refusal_code


class SessionJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        try:
            check_session(user, validated_token)
        except TokenError as exc:
            # A dict detail keeps the code in the body, as simplejwt does.
            raise AuthenticationFailed({"detail": str(exc), "code": refusal_code(exc)})
        return user
