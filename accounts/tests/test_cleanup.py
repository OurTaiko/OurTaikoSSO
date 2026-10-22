from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import User


class UnverifiedAccountCleanupTests(TestCase):
    def account(self, username, *, days, active=False, verified=False, signed_in=False, staff=False):
        user = User.objects.create_user(
            username=username,
            email=username + "@example.test",
            password="Rhythm!Dev4279",
            is_active=active,
            email_verified=verified,
            is_staff=staff,
        )
        User.objects.filter(pk=user.pk).update(
            date_joined=timezone.now() - timedelta(days=days),
            last_login=timezone.now() if signed_in else None,
        )
        return user

    def test_unverified_accounts_older_than_a_day_are_deleted(self):
        unverified = [
            self.account("abandoned", days=2),
            self.account("activeunverified", days=30, active=True),
        ]
        kept = [
            self.account("fresh", days=0.5),
            self.account("verified", days=30, active=True, verified=True),
            self.account("deactivated", days=30, verified=True),
            self.account("signedin", days=30, active=True, signed_in=True),
            self.account("staff", days=30, staff=True),
        ]
        out = StringIO()
        call_command("cleanup_auth", stdout=out)
        self.assertFalse(User.objects.filter(pk__in=[user.pk for user in unverified]).exists())
        self.assertEqual(
            set(User.objects.values_list("username", flat=True)), {user.username for user in kept}
        )
        self.assertIn("2 unverified account(s) deleted", out.getvalue())

    def test_a_deleted_registration_frees_its_name_and_email(self):
        self.account("again", days=2)
        call_command("cleanup_auth", stdout=StringIO())
        response = self.client.post(
            "/register/",
            {
                "username": "again",
                "nickname": "again",
                "email": "again@example.test",
                "password1": "Rhythm!Dev4279",
                "password2": "Rhythm!Dev4279",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username="again", is_active=False).exists())
