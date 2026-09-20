"""Exercise the running local HTTP server with a temporary account and clean up."""

import json
import secrets
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError

from accounts.models import BrowserSession, User


class Command(BaseCommand):
    help = "Test live local HTTP login/profile/password/token flows with a disposable account."

    def handle(self, *args, **options):
        if not settings.DEBUG or urlsplit(settings.SSO_BASE_URL).hostname not in ("127.0.0.1", "localhost"):
            raise CommandError("Only allowed against the local development server.")
        config = json.loads((settings.BASE_DIR / ".data/dev-clients.json").read_text())
        password = secrets.token_urlsafe(24)
        user = User.objects.create_user(
            username="smoke" + secrets.token_hex(6),
            nickname="HTTP 联调",
            password=password,
            email_verified=True,
        )
        with requests.Session() as browser:
            browser.trust_env = False
            base = settings.SSO_BASE_URL

            def api(path, body, service="fanmade"):
                credentials = config[service]
                return browser.post(
                    base + "/internal/v1/" + path,
                    json=body,
                    headers={
                        "X-Service-ID": credentials["serviceId"],
                        "X-Service-Key": credentials["serviceKey"],
                    },
                    timeout=10,
                )

            def form(path, data):
                browser.get(base + path, timeout=10).raise_for_status()
                return browser.post(
                    base + path,
                    data={**data, "csrfmiddlewaretoken": browser.cookies[settings.CSRF_COOKIE_NAME]},
                    timeout=10,
                )

            try:
                response = api("game/login", {"username": user.username, "password": password})
                response.raise_for_status()
                token = response.json()["accessToken"]
                assert len(token) == 64
                assert api("game/introspect", {"accessToken": token}).json()["user"]["id"] == user.pk
                assert api("game/introspect", {"accessToken": token}, "ese").status_code == 401
                response = form("/login/", {"username": user.username, "password": password})
                assert response.status_code == 200 and "你好，HTTP 联调" in response.text
                response = form("/profile/", {"nickname": "HTTP 联调完成"})
                assert response.status_code == 200 and "昵称已更新" in response.text
                assert (
                    api("users/lookup", {"userIds": [user.pk]}).json()["users"][0]["nickname"]
                    == "HTTP 联调完成"
                )
                new_password = secrets.token_urlsafe(24)
                response = form(
                    "/security/",
                    {"old_password": password, "new_password1": new_password, "new_password2": new_password},
                )
                assert response.status_code == 200 and "密码已更新" in response.text
                assert api("game/introspect", {"accessToken": token}).status_code == 401
                assert api("game/login", {"username": user.username, "password": password}).status_code == 401
                response = api("game/login", {"username": user.username, "password": new_password})
                response.raise_for_status()
                new_token = response.json()["accessToken"]
                assert api("game/revoke", {"accessToken": new_token}).status_code == 200
                assert api("game/introspect", {"accessToken": new_token}).status_code == 401
            finally:
                Session.objects.filter(
                    session_key__in=BrowserSession.objects.filter(user=user).values("session_key")
                ).delete()
                user.delete()
        self.stdout.write(
            "PASS: real HTTP/PostgreSQL game login, audience, web login/CSRF, nickname, password change, old-password rejection, and token revocation. Temporary account removed."
        )
