import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
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
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
    "PKCE_REQUIRED": True,
    "SCOPES": {"openid": "确认你的账号身份", "profile": "读取你的昵称和登录名"},
    "DEFAULT_SCOPES": ["openid"],
    "ACCESS_TOKEN_EXPIRE_SECONDS": 3600,
    "AUTHORIZATION_CODE_EXPIRE_SECONDS": 60,
    "ROTATE_REFRESH_TOKEN": True,
    "ALLOWED_REDIRECT_URI_SCHEMES": ["http", "https"] if DEBUG else ["https"],
    "REQUEST_APPROVAL_PROMPT": "auto",
}
if DEBUG:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
