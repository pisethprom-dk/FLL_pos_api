# v1.0.1
from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from users.models import User
from users.passwords import make_initial_password
from users.permissions import IsAdmin
from users.serializers import (
    LoginSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from users.tokens import (
    clear_refresh_cookie,
    issue_refresh,
    rotate_refresh,
    set_refresh_cookie,
)


class LoginView(APIView):
    """Access token in the body, refresh token in an httpOnly cookie."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        user.touch_login()

        refresh = issue_refresh(user)
        body = {
            "access": str(refresh.access_token),
            "user": MeSerializer(user).data,
        }
        return set_refresh_cookie(Response(body), refresh)


class RefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if not raw:
            return Response(
                {"detail": "Not signed in."}, status=status.HTTP_401_UNAUTHORIZED
            )
        try:
            refresh, user = rotate_refresh(raw)
        except TokenError as exc:
            response = Response(
                {"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED
            )
            return clear_refresh_cookie(response)

        body = {"access": str(refresh.access_token), "user": MeSerializer(user).data}
        return set_refresh_cookie(Response(body), refresh)


class LogoutView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if raw:
            try:
                from rest_framework_simplejwt.tokens import RefreshToken

                RefreshToken(raw).blacklist()
            except Exception:
                pass  # already expired or invalid; clearing the cookie is enough
        return clear_refresh_cookie(Response({"detail": "Signed out."}))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password changed."})


class UserViewSet(viewsets.ModelViewSet):
    """Admin only. Users are deactivated, never deleted."""

    queryset = User.objects.all()
    permission_classes = [IsAdmin]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        return UserCreateSerializer if self.action == "create" else UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        data = UserSerializer(user).data
        # Shown once so the Admin can pass it on; never stored in clear.
        data["initial_password"] = serializer._initial_password
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        if user == request.user:
            return Response(
                {"detail": "You cannot switch off your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=["is_active"])
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        password = make_initial_password()
        user.set_password(password)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        return Response({"initial_password": password})
