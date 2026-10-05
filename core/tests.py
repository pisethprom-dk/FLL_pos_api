# v1.0.0 — smoke checks that catch deployment problems before a container dies
from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class SystemCheckTests(TestCase):
    """Run Django's own checks as a test.

    The entrypoint runs `migrate`, which runs these checks, and a failure kills
    the container before the server starts — which looks like a refused
    connection rather than an error. Catching it here instead.

    This is what would have caught Pillow missing from requirements.txt: five
    ImageFields across company and catalogue raise fields.E210 without it.
    """

    def test_no_system_check_errors(self):
        out = StringIO()
        call_command("check", stdout=out, stderr=out)
        self.assertNotIn("ERRORS", out.getvalue())

    def test_imagefield_support_is_installed(self):
        try:
            import PIL  # noqa: F401
        except ImportError:  # pragma: no cover
            self.fail(
                "Pillow is not installed, but the models use ImageField. "
                "Add it to requirements.txt."
            )

    def test_no_missing_migrations(self):
        """A model changed without makemigrations fails the container too."""
        out = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
        except SystemExit:
            self.fail(f"Models have changed without a migration:\n{out.getvalue()}")
