import os
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 40:
    raise ImproperlyConfigured("Run scripts/setup_dev.py to create local secrets before starting.")
SSO_BASE_URL = os.getenv("SSO_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
if not DEBUG and not SSO_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured("SSO_BASE_URL must use HTTPS outside local development.")
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "oauth2_provider",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "accounts.middleware.UploadLimitMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.AccountLocaleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.ResponsePolicyMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context.site",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / '.data' / 'dev.sqlite3'}",
        conn_max_age=0,
    )
}
AUTH_USER_MODEL = "accounts.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "accounts.hashers.LegacyGoBCryptHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
LANGUAGES = [("zh-hans", "简体中文"), ("en", "English"), ("ja", "日本語"), ("ko", "한국어")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# Avatars are re-encoded to WebP and stored under the "sso/" prefix of the bucket.
# Credentials come from the AWS SDK chain (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or a role).
S3_BUCKET = os.getenv("S3_BUCKET", "")
# Complete CDN prefix for avatars (including /sso/avatars).
AVATAR_CDN_BASE_URL = os.getenv("AVATAR_CDN_BASE_URL", "").rstrip("/")
if AVATAR_CDN_BASE_URL:
    parsed = urlsplit(AVATAR_CDN_BASE_URL)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or "?" in AVATAR_CDN_BASE_URL
        or "#" in AVATAR_CDN_BASE_URL
        or any(c.isspace() or c in "\\;\"'" for c in AVATAR_CDN_BASE_URL)
    ):
        raise ImproperlyConfigured(
            "AVATAR_CDN_BASE_URL must be an HTTPS URL without credentials, query or fragment."
        )
if S3_BUCKET:
    STORAGES["avatars"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": S3_BUCKET,
            "region_name": os.getenv("AWS_REGION") or None,
            "endpoint_url": os.getenv("S3_ENDPOINT_URL") or None,
            "location": os.getenv("S3_PREFIX", "sso").strip("/"),
            "default_acl": None,
            "file_overwrite": True,
            "object_parameters": {
                "ContentType": "image/webp",
                "CacheControl": "public, max-age=60",
            },
        },
    }
elif DEBUG:
    STORAGES["avatars"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": BASE_DIR / ".data" / "s3" / "sso"},  # Mirrors the bucket layout.
    }
else:
    raise ImproperlyConfigured("S3_BUCKET is required for avatar storage outside local development.")
AVATAR_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Enable only behind a proxy that overwrites X-Forwarded-Proto. The container
# binds to loopback, so untrusted public clients cannot supply this header directly.
if os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_NAME = "ourtaiko_sso_session"
CSRF_COOKIE_NAME = "ourtaiko_sso_csrf"
SESSION_COOKIE_AGE = 7 * 86400
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_TRUSTED_ORIGINS = [SSO_BASE_URL]
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 32768
PASSWORD_RESET_TIMEOUT = 1800
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.filebased.EmailBackend")
EMAIL_FILE_PATH = BASE_DIR / ".data" / "emails"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "OurTaiko <no-reply@localhost>")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "false").lower() == "true"
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_TIMEOUT = 10
key_path = Path(os.getenv("OIDC_PRIVATE_KEY_FILE", ".data/oidc-private.pem"))
if not key_path.is_absolute():
    key_path = BASE_DIR / key_path
OAUTH2_PROVIDER = {
    "OIDC_ENABLED": True,
    "OIDC_RSA_PRIVATE_KEY": key_path.read_text() if key_path.exists() else "",
    "OIDC_ISS_ENDPOINT": SSO_BASE_URL,
    "OAUTH2_VALIDATOR_CLASS": "accounts.oidc.AccountValidator",
    "APPLICATION_ADMIN_CLASS": "accounts.admin.UnifiedApplicationAdmin",
    "PKCE_REQUIRED": True,
    "SCOPES": {"openid": _("确认你的账号身份"), "profile": _("读取你的昵称、登录名和偏好语言")},
    "DEFAULT_SCOPES": ["openid"],
    "ACCESS_TOKEN_EXPIRE_SECONDS": 3600,
    "AUTHORIZATION_CODE_EXPIRE_SECONDS": 60,
    "ROTATE_REFRESH_TOKEN": True,
    "ALLOWED_REDIRECT_URI_SCHEMES": ["http", "https"] if DEBUG else ["https"],
    "REQUEST_APPROVAL_PROMPT": "auto",
}
if DEBUG:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# Explicitly identify the stock OAuth application for migration dependencies.
OAUTH2_PROVIDER_APPLICATION_MODEL = "oauth2_provider.Application"
