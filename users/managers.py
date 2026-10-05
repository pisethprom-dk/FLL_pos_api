# v1.0.0
from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, username, password, **extra):
        if not username:
            raise ValueError("A username is required.")
        user = self.model(username=username, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, password=None, **extra):
        extra.setdefault("role", "SELLER")
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(username, password, **extra)

    def create_superuser(self, username, password=None, **extra):
        extra.setdefault("role", "ADMIN")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("must_change_password", False)
        extra.setdefault("full_name", username)
        if extra.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create(username, password, **extra)
