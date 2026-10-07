# v1.0.0 — a warranty claim: what a customer has brought back under warranty
# and where it has got to. A tracking log, not a transaction — no invoice
# link, no money, no stock effect. The product is kept as text, so an item
# not in the catalogue (or since retired) can still be logged; the screen may
# fill it from a catalogue product. A claim is known by the number on the
# warranty card, which is not unique: one card can be claimed twice.
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class ClaimStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    SENT_FOR_REPAIR = "SENT_FOR_REPAIR", "Sent for repair"
    READY = "READY", "Ready for collection"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"


# The customer is still waiting: with the shop, with the repairer, or ready.
OPEN_STATUSES = (ClaimStatus.RECEIVED, ClaimStatus.SENT_FOR_REPAIR, ClaimStatus.READY)


class WarrantyClaim(TimeStampedModel):
    """Any status may follow any other — it records where the item is."""

    warranty_number = models.CharField(
        max_length=40, help_text="From the warranty card or the supplier's sticker."
    )
    product_code = models.CharField(max_length=30, blank=True)
    product_name = models.CharField(max_length=200)
    warranty_months = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(120)], help_text="Zero when the item has none."
    )
    expiry_date = models.DateField(null=True, blank=True)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=ClaimStatus.choices, default=ClaimStatus.RECEIVED, db_index=True
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.warranty_number} — {self.product_name}"

    @property
    def out_of_warranty(self):
        """Claimed after the warranty ended: its end date is before the day it was logged."""
        logged = timezone.localdate(self.created_at) if self.created_at else timezone.localdate()
        return self.expiry_date is not None and self.expiry_date < logged
