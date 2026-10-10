import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application, RefreshToken
from oauthlib.oauth2 import InvalidGrantError

from accounts.models import ApplicationAccess, User
from accounts.oidc import AccountValidator


class RefreshTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="renew", password="OldRhythm!4279")
        self.app = Application.objects.create(
            name="Renewable website",
            client_id="renew-web",
            client_secret="r" * 64,
            client_type="confidential",
            authorization_grant_type="authorization-code",
            algorithm="RS256",
        )
        ApplicationAccess.objects.create(application=self.app, allow_web=True)
        self.access = AccessToken.objects.create(
            user=self.user,
            application=self.app,
            token="old-access",
            scope="openid profile",
            expires=timezone.now() - timedelta(days=2),
        )
        self.refresh = RefreshToken.objects.create(
            user=self.user,
            application=self.app,
            token="old-refresh",
            token_family=uuid.uuid4(),
            access_token=self.access,
        )

    def renew(self, token="old-refresh"):
        return self.client.post(
            "/o/token/",
            {
                "grant_type": "refresh_token",
                "refresh_token": token,
                "client_id": self.app.client_id,
                "client_secret": "r" * 64,
            },
        )

    def revoke(self, body):
        return self.client.post(
            "/internal/v1/web/revoke",
            body,
            content_type="application/json",
            HTTP_X_SERVICE_ID=self.app.client_id,
            HTTP_X_SERVICE_KEY="r" * 64,
        )

    def test_refresh_after_days_away_rotates_and_rejects_replay(self):
        response = self.renew()
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result["expires_in"], 3600)
        self.assertNotEqual(result["refresh_token"], "old-refresh")
        self.assertEqual(self.renew().status_code, 400)
        self.assertEqual(self.renew(result["refresh_token"]).status_code, 200)

    def test_password_change_revokes_current_access_and_refresh(self):
        result = self.renew().json()
        self.client.force_login(self.user)
        response = self.client.post(
            "/security/",
            {
                "old_password": "OldRhythm!4279",
                "new_password1": "NewRhythm!6382",
                "new_password2": "NewRhythm!6382",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AccessToken.objects.filter(user=self.user).exists())
        self.assertFalse(RefreshToken.objects.filter(user=self.user).exists())
        self.assertEqual(self.renew(result["refresh_token"]).status_code, 400)

    def test_expired_access_still_lists_revocable_authorization(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/connections/"), "Renewable website")
        response = self.client.post("/connections/revoke/", {"kind": "web", "key": self.app.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.renew().status_code, 400)
        self.assertFalse(AccessToken.objects.filter(user=self.user).exists())

    def test_logout_revokes_refresh_even_after_access_expired(self):
        self.assertEqual(
            self.revoke({"accessToken": "old-access", "refreshToken": "old-refresh"}).status_code, 200
        )
        self.assertEqual(self.renew().status_code, 400)

    def test_legacy_logout_also_revokes_bound_refresh(self):
        self.assertEqual(self.revoke({"accessToken": "old-access"}).status_code, 200)
        self.assertEqual(self.renew().status_code, 400)

    def test_logout_can_revoke_rotated_family_using_previous_refresh(self):
        result = self.renew().json()
        self.assertEqual(
            self.revoke({"accessToken": "old-access", "refreshToken": "old-refresh"}).status_code, 200
        )
        self.assertEqual(self.renew(result["refresh_token"]).status_code, 400)

    def test_expired_refresh_and_disabled_user_are_rejected(self):
        self.access.expires = timezone.now() - timedelta(days=8)
        self.access.save()
        self.assertEqual(self.renew().status_code, 400)
        self.access.expires = timezone.now() - timedelta(hours=2)
        self.access.save()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.renew().status_code, 400)

    def test_revocation_between_validation_and_save_cannot_mint_tokens(self):
        validator = AccountValidator()
        request = SimpleNamespace(
            redirect_uri=None,
            client_id=self.app.client_id,
            scopes=["openid"],
            response_type=None,
            response_mode=None,
            state=None,
            user=self.user,
            client=self.app,
            grant_type="refresh_token",
            refresh_token="old-refresh",
        )
        self.assertTrue(validator.validate_refresh_token("old-refresh", self.app, request))
        RefreshToken.objects.filter(user=self.user).delete()
        with (
            self.assertRaises(InvalidGrantError),
            patch.object(AccountValidator.__bases__[0], "save_bearer_token") as save,
        ):
            validator.save_bearer_token({}, request)
        save.assert_not_called()

    def test_password_change_between_validation_and_save_is_rejected(self):
        validator = AccountValidator()
        request = SimpleNamespace(
            redirect_uri=None,
            client_id=self.app.client_id,
            scopes=["openid"],
            response_type=None,
            response_mode=None,
            state=None,
            user=self.user,
            client=self.app,
            grant_type="refresh_token",
            refresh_token="old-refresh",
        )
        self.assertTrue(validator.validate_refresh_token("old-refresh", self.app, request))
        changed = User.objects.get(pk=self.user.pk)
        changed.set_password("NewRhythm!6382")
        changed.save()
        with self.assertRaises(InvalidGrantError):
            validator.save_bearer_token({}, request)
