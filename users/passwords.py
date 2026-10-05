# v1.0.0 — initial password generation.
#
# BaseUserManager.make_random_password() was removed in Django 5.1, so this
# does the job explicitly. The alphabet leaves out characters that are easy to
# confuse when an Admin reads a password aloud to a seller: O/0, l/1/I.
from django.utils.crypto import get_random_string

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def make_initial_password(length=12):
    return get_random_string(length, ALPHABET)
