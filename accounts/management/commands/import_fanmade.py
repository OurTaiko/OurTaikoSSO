import os
import re
from urllib.parse import urlsplit

import psycopg
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import ClientRole, GameClient, User


class Command(BaseCommand):
    help = "Read-only import from local Fanmade PostgreSQL. Dry-run unless --apply is supplied."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        source = os.environ.get("FANMADE_SOURCE_DATABASE_URL", "")
        if not source or urlsplit(source).hostname not in ("localhost", "127.0.0.1", "::1"):
            raise CommandError("FANMADE_SOURCE_DATABASE_URL must reference a loopback database.")
        with psycopg.connect(source, options="-c default_transaction_read_only=on") as connection:
            rows = connection.execute(
                "SELECT id, username, nickname, password_hash, COALESCE(email,''), "
                "email_verified_at IS NOT NULL, is_admin, created_at FROM users"
            ).fetchall()
        imported = skipped = 0
        with transaction.atomic():
            client = GameClient.objects.filter(pk="fanmade").first()
            if not client:
                raise CommandError("Run provision_fanmade first to create the Fanmade service client.")
            for uid, username, nickname, password, email, verified, admin, created in rows:
                if not re.fullmatch(r"[0-9a-f]{32}", uid) or not password.startswith(
                    ("$2a$", "$2b$", "$2y$")
                ):
                    raise CommandError(
                        "Source contains an unsupported ID or password hash; no import applied."
                    )
                existing = User.objects.filter(pk=uid).first()
                if existing:
                    if existing.username != username:
                        raise CommandError("User ID conflict; no import applied.")
                    skipped += 1
                    continue
                if User.objects.filter(username=username).exists() or (
                    email and User.objects.filter(email__iexact=email).exists()
                ):
                    raise CommandError(
                        "Username or email conflict; no import applied. Review the source locally."
                    )
                user = User.objects.create(
                    id=uid,
                    username=username,
                    nickname=nickname,
                    password="bcrypt$" + password,
                    email=email.lower(),
                    email_verified=verified,
                    date_joined=created,
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                )
                if admin:
                    ClientRole.objects.create(user=user, client=client, is_admin=True)
                imported += 1
            if not options["apply"]:
                transaction.set_rollback(True)
        mode = "Imported" if options["apply"] else "Dry-run: would import"
        self.stdout.write(
            f"{mode} {imported} accounts; {skipped} already present. Source was read-only. Existing SSO accounts were not overwritten."
        )
