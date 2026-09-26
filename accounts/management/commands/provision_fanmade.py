"""Provision server-owned clients using secrets already held in private environment files."""

import os
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oauth2_provider.models import Application

from accounts.models import ApplicationAccess


class Command(BaseCommand):
    help = "Provision the Fanmade service and confidential OIDC client from environment variables."

    @transaction.atomic
    def handle(self, *args, **options):
        client_id = os.getenv("FANMADE_OIDC_CLIENT_ID", "ourtaiko-fanmade-web")
        client_secret = os.getenv("FANMADE_OIDC_CLIENT_SECRET", "")
        redirect_uri = os.getenv("FANMADE_REDIRECT_URI", "")
        if not 40 <= len(client_secret) <= 1024:
            raise CommandError("Set a strong FANMADE_OIDC_CLIENT_SECRET.")
        parsed = urlsplit(redirect_uri)
        if (
            not parsed.netloc
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme != "https"
                and not (
                    settings.DEBUG
                    and parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1"}
                )
            )
        ):
            raise CommandError("Set an exact HTTPS FANMADE_REDIRECT_URI.")
        app, created = Application.objects.get_or_create(
            client_id=client_id,
            defaults={
                "name": "OurTaiko Fanmade",
                "client_secret": client_secret,
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "algorithm": "RS256",
                "redirect_uris": redirect_uri,
            },
        )
        if not created:
            from django.contrib.auth.hashers import check_password

            if not check_password(client_secret, app.client_secret) or app.redirect_uris != redirect_uri:
                raise CommandError("Existing OAuth client differs; refusing to overwrite silently.")
        ApplicationAccess.objects.get_or_create(
            application=app,
            defaults={
                "allow_game": True,
                "allow_profiles": True,
                "allow_web": True,
            },
        )
        self.stdout.write("Fanmade service and OIDC client provisioned. No credentials printed.")
