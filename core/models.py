# v1.0.0 — abstract bases shared by every app
from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Created and updated stamps, plus who did it.

    Audit columns use PROTECT nowhere — they are nullable and SET_NULL is wrong
    for history, so a user is deactivated rather than deleted (see users.User).
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True


class ActivatableModel(models.Model):
    """Records with history are deactivated, never deleted."""

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True
