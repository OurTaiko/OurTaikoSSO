import json
import os
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oauth2_provider.models import Application

from accounts.models import GameClient, User
from accounts.security import digest


class Command(BaseCommand):
    help = "Create a local test account and service clients. Secrets are written to private ignored files."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_dev requires DJANGO_DEBUG=true")
        os.umask(0o077)
        data = settings.BASE_DIR / ".data"
        credentials = data / "dev-clients.json"
        config = json.loads(credentials.read_text()) if credentials.exists() else {}
        for client_id, name in [("fanmade", "OurTaiko Fanmade"), ("ese", "Every Song Ever")]:
            if GameClient.objects.filter(pk=client_id).exists():
                if client_id not in config:
                    raise CommandError(
                        f"Client {client_id} exists but its local secret file is absent; refusing to rotate silently."
                    )
                continue
            key = secrets.token_hex(32)
            GameClient.objects.create(id=client_id, name=name, secret_hash=digest(key))
            config[client_id] = {"serviceId": client_id, "serviceKey": key}
        if not User.objects.filter(username="ssoDev").exists():
            password = secrets.token_urlsafe(18)
            User.objects.create_user(
                username="ssoDev",
                nickname="咚酱",
                email="sso-dev@example.test",
                email_verified=True,
                password=password,
            )
            (data / "dev-access.txt").write_text(
                f"仅供本地开发\nURL: {settings.SSO_BASE_URL}\n登录名: ssoDev\n密码: {password}\n"
            )
        if not Application.objects.filter(client_id="ourtaiko-dev-web").exists():
            secret = secrets.token_urlsafe(48)
            Application.objects.create(
                name="OurTaiko 登录演示",
                client_id="ourtaiko-dev-web",
                client_secret=secret,
                client_type=Application.CLIENT_CONFIDENTIAL,
                authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
                algorithm="RS256",
                redirect_uris=settings.SSO_BASE_URL + "/dev/demo/callback/",
            )
            config["webDemo"] = {"clientId": "ourtaiko-dev-web", "clientSecret": secret}
        if not Application.objects.filter(client_id="ourtaiko-fanmade-web").exists():
            secret = secrets.token_urlsafe(48)
            Application.objects.create(
                name="OurTaiko Fanmade",
                client_id="ourtaiko-fanmade-web",
                client_secret=secret,
                client_type=Application.CLIENT_CONFIDENTIAL,
                authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
                algorithm="RS256",
                redirect_uris="http://127.0.0.1:5173/api/v1/auth/sso/callback",
            )
            config["fanmadeWeb"] = {"clientId": "ourtaiko-fanmade-web", "clientSecret": secret}
        if "fanmadeWeb" not in config:
            raise CommandError("Fanmade web client secret file is missing; refusing to rotate silently.")
        GameClient.objects.filter(pk="fanmade").update(web_client_id="ourtaiko-fanmade-web")
        credentials.write_text(json.dumps(config, indent=2))
        self.stdout.write(
            "Development account and clients ready. Credentials: .data/dev-access.txt and .data/dev-clients.json"
        )
