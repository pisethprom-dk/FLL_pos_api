# v1.1.0 — POS backend settings
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    AUTH_COOKIE_SECURE=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DEBUG")

# Production (DEBUG off) refuses to start without these; development falls
# back to what docker-compose runs. SECRET_KEY also signs every JWT.
_REQUIRED = environ.Env.NOTSET
SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key" if DEBUG else _REQUIRED)
_DEV_DATABASE_URL = "postgres://pos:pos@db:5432/pos"

ALLOWED_HOSTS = env("ALLOWED_HOSTS")
# The Django admin's login form; the API runs on JWT and needs no CSRF.
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# nginx terminates TLS and sets X-Forwarded-Proto itself, overwriting any a
# client sent (deploy/nginx/pos.conf), so Django may trust it.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# The Django admin's cookies; the refresh cookie follows AUTH_COOKIE_SECURE.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "core",
    "users",
    "company",
    "catalogue",
    "partners",
    "inventory",
    "sales",
    "warranty",
    "reports",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# postgres://user:password@host:5432/name
DATABASES = {
    "default": env.db("DATABASE_URL", default=_DEV_DATABASE_URL if DEBUG else _REQUIRED),
}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Phnom Penh. Every "today's sales" query depends on this being right.
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Phnom_Penh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # JWT, refused once the user has signed in on another device
        "users.authentication.SessionJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# Access lives in Angular memory for 15 minutes.
# Refresh lives in an httpOnly cookie for 60 minutes and rotates on every use,
# so an idle till is logged out after 60 minutes of no activity.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(minutes=60),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# Regardless of activity, a login is dead after this. A till left on overnight
# is logged out by morning.
AUTH_ABSOLUTE_SESSION_HOURS = 12

AUTH_COOKIE_NAME = "pos_refresh"
AUTH_COOKIE_PATH = "/api/auth/"
AUTH_COOKIE_SECURE = env("AUTH_COOKIE_SECURE")
AUTH_COOKIE_SAMESITE = "Lax"

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

SPECTACULAR_SETTINGS = {
    "TITLE": "POS API",
    "DESCRIPTION": "Point of sale back office for a single tool shop.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Separate request and response models, so a generated client does not
    # ask the caller for read-only fields such as id or number.
    "COMPONENT_SPLIT_REQUEST": True,
    # Readable enum names in the generated client, instead of hashed ones.
    "ENUM_NAME_OVERRIDES": {
        "DraftPostedStatusEnum": "inventory.models.DocStatus",
        "QuoteStatusEnum": "sales.models.QuoteStatus",
        "InvoiceStatusEnum": "sales.models.InvoiceStatus",
        "PaymentStatusEnum": "sales.models.PaymentStatus",
        "TenderKindEnum": "sales.money.TENDER_KINDS",
        "PaymentTenderEnum": "sales.models.PaymentTender",
        "ClaimStatusEnum": "warranty.models.ClaimStatus",
        "StockStatusEnum": "reports.services.StockStatus",
    },
}
