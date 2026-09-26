import json
import os
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oauth2_provider.models import Application

from accounts.models import ApplicationAccess, User


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
        fanmade = Application.objects.get(client_id="ourtaiko-fanmade-web")
        ApplicationAccess.objects.get_or_create(
            application=fanmade,
            defaults={
                "allow_game": True,
                "allow_profiles": True,
                "allow_web": True,
            },
        )
        # Existing private service configs continue through their migrated legacy aliases.
        config.setdefault(
            "fanmade", {"serviceId": fanmade.client_id, "serviceKey": config["fanmadeWeb"]["clientSecret"]}
        )
        if not Application.objects.filter(client_id="ese").exists():
            secret = secrets.token_hex(32)
            ese = Application.objects.create(
                name="Every Song Ever",
                client_id="ese",
                client_secret=secret,
                client_type="confidential",
                authorization_grant_type="client-credentials",
                algorithm="RS256",
            )
            ApplicationAccess.objects.create(application=ese, allow_game=True, allow_profiles=True)
            config["ese"] = {"serviceId": "ese", "serviceKey": secret}
        elif "ese" not in config:
            raise CommandError(
                "ESE already exists but its private credential file is missing; refusing to rotate."
            )
        credentials.write_text(json.dumps(config, indent=2))
        self.stdout.write(
            "Development account and clients ready. Credentials: .data/dev-access.txt and .data/dev-clients.json"
        )
