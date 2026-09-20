from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import BrowserSession, GameSession, RateBucket


class Command(BaseCommand):
    help = "Remove expired session/rate-limit records; suitable for a daily maintenance job."

    def handle(self, *args, **options):
        call_command("clearsessions")
        call_command("cleartokens")
        now = timezone.now()
        GameSession.objects.filter(expires_at__lte=now).delete()
        RateBucket.objects.filter(expires_at__lte=now).delete()
        BrowserSession.objects.exclude(session_key__in=Session.objects.values("session_key")).delete()
        self.stdout.write("Expired authentication records removed.")
