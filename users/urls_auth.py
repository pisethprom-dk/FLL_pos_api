# v1.0.0 — /api/auth/
from django.urls import path

from users.views import (
    LoginView,
    LogoutView,
    MeView,
    PasswordChangeView,
    RefreshView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="auth-me"),
    path("password/", PasswordChangeView.as_view(), name="auth-password"),
]
