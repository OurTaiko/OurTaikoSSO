from .settings import *  # noqa: F403

# Never touch the user's development or existing Fanmade database in automated tests.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher", "accounts.hashers.LegacyGoBCryptHasher"]
ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
