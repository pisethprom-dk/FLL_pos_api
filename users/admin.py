# v1.0.0
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from users.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "full_name", "role", "is_active", "last_login_at"]
    list_filter = ["role", "is_active"]
    search_fields = ["username", "full_name", "phone"]
    ordering = ["username"]

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Person", {"fields": ("full_name", "full_name_kh", "phone")}),
        ("Access", {"fields": ("role", "is_active", "is_staff", "must_change_password")}),
        ("History", {"fields": ("last_login_at", "created_at", "created_by")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "full_name", "role", "password1", "password2"),
        }),
    )
    readonly_fields = ["last_login_at", "created_at", "created_by"]
