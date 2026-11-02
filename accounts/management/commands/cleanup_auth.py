from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import BrowserSession, ExternalAudit, GameAccount, GameSession, RateBucket, User


class Command(BaseCommand):
    help = (
        "Remove expired session/rate-limit/audit records and accounts that never confirmed "
        "their email; suitable for a daily maintenance job."
    )

    def handle(self, *args, **options):
        call_command("clearsessions")
        call_command("cleartokens")
        now = timezone.now()
        GameSession.objects.filter(expires_at__lte=now).delete()
        GameAccount.objects.filter(expires_at__lte=now).delete()
        RateBucket.objects.filter(expires_at__lte=now).delete()
        BrowserSession.objects.exclude(session_key__in=Session.objects.values("session_key")).delete()
        ExternalAudit.objects.filter(created_at__lte=now - timedelta(days=settings.EXTERNAL_AUDIT_DAYS)).delete()
        # Accounts whose email was never confirmed and that never signed in, past the grace
        # period. Staff accounts are left alone.
        _, deleted = User.objects.filter(
            email_verified=False,
            last_login__isnull=True,
            is_staff=False,
            is_superuser=False,
            date_joined__lte=now - timedelta(days=settings.UNVERIFIED_ACCOUNT_DAYS),
        ).delete()
        unverified = deleted.get(User._meta.label, 0)
        self.stdout.write(
            f"Expired authentication records removed; {unverified} unverified account(s) deleted."
        )
