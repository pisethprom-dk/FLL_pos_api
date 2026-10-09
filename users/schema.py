# v1.0.0 — drf-spectacular's JWT scheme does not cover subclasses; this tells it
# SessionJWTAuthentication is the same bearer scheme, under the same name.
# Imported from UsersConfig.ready().
from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class SessionJWTScheme(SimpleJWTScheme):
    target_class = "users.authentication.SessionJWTAuthentication"
