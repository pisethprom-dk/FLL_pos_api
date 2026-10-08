# v1.1.0 — custom user. AUTH_USER_MODEL is set before the first migration on
# purpose: swapping it on a live database is painful.
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from users.managers import UserManager


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    SELLER = "SELLER", "Seller"


class User(AbstractBaseUser, PermissionsMixin):
    """Shop staff.

    Login is by username, not email — shop staff often have no work email.
    Users are never deleted; sales hold PROTECT foreign keys to them.
    """

    username = models.CharField(max_length=50, unique=True)
    full_name = models.CharField(max_length=150)
    full_name_kh = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.SELLER,
        db_index=True,
    )

    is_active = models.BooleanField(default=True, db_index=True)
    is_staff = models.BooleanField(default=False)  # Django admin access only
    must_change_password = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    # One sign-in at a time: every token carries this as its `sid`, and a new
    # sign-in replaces it, so the device signed in before is refused.
    current_session = models.UUIDField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return f"{self.full_name} ({self.username})"

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def is_seller(self):
        return self.role == Role.SELLER

    def touch_login(self):
        self.last_login_at = timezone.now()
        self.save(update_fields=["last_login_at"])

    def start_session(self):
        """A new sign-in. Whatever session this user had before stops working."""
        self.current_session = uuid.uuid4()
        self.save(update_fields=["current_session"])
        return str(self.current_session)
