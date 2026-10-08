# v1.1.0
from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        from users import schema  # noqa: F401  registers the JWT scheme extension
