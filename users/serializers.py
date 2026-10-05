# v1.0.1
from django.contrib.auth import authenticate, password_validation
from rest_framework import serializers

from users.models import User
from users.passwords import make_initial_password
from users.scopes import scopes_for


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "username", "full_name", "full_name_kh", "phone",
            "role", "is_active", "must_change_password",
            "last_login_at", "created_at",
        ]
        read_only_fields = ["id", "last_login_at", "created_at", "must_change_password"]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "full_name", "full_name_kh", "phone",
            "role", "is_active", "password",
        ]

    def validate_password(self, value):
        if value:
            password_validation.validate_password(value)
        return value

    def create(self, validated):
        password = validated.pop("password", "") or make_initial_password()
        user = User.objects.create_user(
            password=password,
            created_by=self.context["request"].user,
            **validated,
        )
        # The new user must set their own password on first login.
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        self._initial_password = password
        return user


class MeSerializer(serializers.ModelSerializer):
    scopes = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "full_name", "full_name_kh",
            "role", "must_change_password", "scopes",
        ]

    def get_scopes(self, obj):
        return scopes_for(obj)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            # Deliberately vague: do not say which half was wrong.
            raise serializers.ValidationError("Username or password is not correct.")
        if not user.is_active:
            raise serializers.ValidationError("This account is switched off.")
        attrs["user"] = user
        return attrs


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("That is not your current password.")
        return value

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context["request"].user)
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return user
