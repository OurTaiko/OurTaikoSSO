"""Isolated browser integration harness; never imported by production.

The real Authlib transport reaches a local running oidc-provider. Discovery still
advertises the fixed HTTPS issuer and endpoints; no JWT/claims are stubbed.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server

import requests

if os.environ.get("TAIKO_ALLOW_TEST_DB_MUTATION") != "1":
    raise RuntimeError("Explicit test mutation opt-in required")
path = Path(os.environ["SAKURA_WIRE_DIR"])
if not str(path).startswith("/var/tmp/") or not path.is_dir():
    raise RuntimeError("Private isolated artifact directory required")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.conf import settings
settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": path / "ourtaiko_test.sqlite3"}}
settings.STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                     "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
settings.SESSION_COOKIE_SECURE = True
settings.CSRF_COOKIE_SECURE = True
import django
django.setup()
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connection
from django.http import JsonResponse
from django.urls import path as urlpath
from oauth2_provider.models import Application
from accounts.models import ExternalLink, User
from config.urls import urlpatterns

with connection.cursor() as cursor:
    cursor.execute("PRAGMA database_list")
    actual = cursor.fetchone()[2]
    if Path(actual).name != "ourtaiko_test.sqlite3" or Path(actual).parent != path:
        raise RuntimeError("Actual isolated test database required")
call_command("migrate", verbosity=0)
user = User.objects.create_user(username="wire-linked", nickname="Original linked account",
    email="wire-linked@example.test", email_verified=True, password=None)
app = Application.objects.create(client_id="Sakura", name="Sakura",client_type="confidential",
    authorization_grant_type="authorization-code",redirect_uris="https://viewer.sakura-bot.cn/api/account/oidc/ourtaiko/callback")
link = ExternalLink.objects.create(application=app,user=user,external_id="9007199254740993")
baseline = (user.password, user.auth_version, user.is_staff, user.is_superuser)

def evidence(request):
    current = User.objects.get(pk=user.pk)
    unchanged = baseline == (current.password, current.auth_version, current.is_staff, current.is_superuser)
    mapping = ExternalLink.objects.filter(pk=link.pk, application=app, user=user,
                                          external_id="9007199254740993").exists()
    return JsonResponse({"authenticated":request.user.is_authenticated,
        "user_id":str(request.user.pk) if request.user.is_authenticated else None,
        "users":User.objects.count(),"links":ExternalLink.objects.count(),
        "credentials_and_privileges_unchanged":unchanged,"mapping_unchanged":mapping})
urlpatterns.insert(0, urlpath("_wire/evidence", evidence))

original_send = requests.Session.send
wire_op = os.environ["SAKURA_WIRE_OP"]
def send(session, request, **kwargs):
    parsed = urlsplit(request.url)
    if parsed.netloc != "viewer.sakura-bot.cn" or parsed.scheme != "https":
        raise RuntimeError("Unexpected integration network request")
    request.url = wire_op + parsed.path + ("?" + parsed.query if parsed.query else "")
    request.headers["Host"] = "viewer.sakura-bot.cn"
    request.headers["X-Forwarded-Proto"] = "https"
    # Every signature and claim is verified normally by Authlib.
    kwargs["proxies"] = {}
    return original_send(session, request, **kwargs)
requests.Session.send = send
class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        pass  # callback queries contain codes and state; never print them
server = make_server("127.0.0.1",0,get_wsgi_application(),handler_class=QuietHandler)
print(json.dumps({"port":server.server_port,"user_id":str(user.pk)}),flush=True)
server.serve_forever()
