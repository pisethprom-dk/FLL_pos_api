# v1.1.0
from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.schema import DetailSerializer
from users.models import User
from users.passwords import make_initial_password
from users.permissions import IsAdmin
from users.serializers import (
    InitialPasswordSerializer,
    LoginSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    SessionEndedSerializer,
    SessionSerializer,
    UserCreatedSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from users.tokens import (
    SESSION_ENDED,
    clear_refresh_cookie,
    end_session,
    issue_refresh,
    refusal_code,
    rotate_refresh,
    set_refresh_cookie,
)


class LoginView(APIView):
    """Access token in the body, refresh token in an httpOnly cookie.
    Signing in ends this user's session on any other device."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=LoginSerializer, responses={200: SessionSerializer})
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

    @extend_schema(
        request=None,
        responses={200: SessionSerializer, 401: SessionEndedSerializer},
        description=(
            "Reads the refresh cookie, rotates it, and returns a new access token. "
            "A 401 says why: session_replaced when the user has since signed in "
            "on another device, session_ended otherwise."
        ),
    )
    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if not raw:
            return self.ended("Not signed in.", SESSION_ENDED)
        try:
            refresh, user = rotate_refresh(raw)
        except TokenError as exc:
            return clear_refresh_cookie(self.ended(str(exc), refusal_code(exc)))

        body = {"access": str(refresh.access_token), "user": MeSerializer(user).data}
        return set_refresh_cookie(Response(body), refresh)

    @staticmethod
    def ended(detail, code):
        body = SessionEndedSerializer({"detail": detail, "code": code}).data
        return Response(body, status=status.HTTP_401_UNAUTHORIZED)


class LogoutView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: DetailSerializer})
    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if raw:
            try:
                token = RefreshToken(raw)
                token.blacklist()
            except Exception:
                pass  # already expired or invalid; clearing the cookie is enough
            else:
                end_session(token)  # its access token stops working too
        return clear_refresh_cookie(Response({"detail": "Signed out."}))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: MeSerializer})
    def get(self, request):
        return Response(MeSerializer(request.user).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PasswordChangeSerializer, responses={200: DetailSerializer})
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

    @extend_schema(request=UserCreateSerializer, responses={201: UserCreatedSerializer})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        data = UserSerializer(user).data
        # Shown once so the Admin can pass it on; never stored in clear.
        data["initial_password"] = serializer._initial_password
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={200: UserSerializer, 400: DetailSerializer})
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

    @extend_schema(request=None, responses={200: UserSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=["is_active"])
        return Response(UserSerializer(user).data)

    @extend_schema(request=None, responses={200: InitialPasswordSerializer})
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        password = make_initial_password()
        user.set_password(password)
        user.must_change_password = True
        user.current_session = None  # signed out wherever they are
        user.save(update_fields=["password", "must_change_password", "current_session"])
        return Response({"initial_password": password})
