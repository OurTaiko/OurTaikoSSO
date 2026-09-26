from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from accounts.models import ApplicationAccess, ClientRole, GameSession, User
from accounts.security import digest


class UnifiedAuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", nickname="Player", password="Testing!4279")
        self.app = Application.objects.create(
            name="Fanmade",
            client_id="fanmade-web",
            client_secret="c" * 64,
            client_type="confidential",
            authorization_grant_type="authorization-code",
            algorithm="RS256",
            redirect_uris="http://localhost:9001/callback",
        )
        self.access = ApplicationAccess.objects.create(
            application=self.app,
            allow_game=True,
            allow_web=True,
            allow_profiles=True,
            legacy_service_id="fanmade",
            legacy_secret_hash=digest("a" * 64),
            accept_legacy=True,
        )
        self.token = AccessToken.objects.create(
            application=self.app,
            user=self.user,
            token="website-token",
            scope="openid profile",
            expires=timezone.now() + timedelta(hours=1),
        )

    def api(self, path="users/lookup", body=None, client_id="fanmade-web", key="c" * 64):
        return self.client.post(
            "/internal/v1/" + path,
            body if body is not None else {"userIds": []},
            content_type="application/json",
            HTTP_X_SERVICE_ID=client_id,
            HTTP_X_SERVICE_KEY=key,
        )

    def test_alias_and_canonical_share_sessions_and_live_roles(self):
        login = self.api(
            "game/login", {"username": "player", "password": "Testing!4279"}, "fanmade", "a" * 64
        )
        self.assertEqual(login.status_code, 200)
        body = {"accessToken": login.json()["accessToken"]}
        self.assertEqual(GameSession.objects.get().client_id, self.app.pk)
        self.assertFalse(self.api("game/introspect", body).json()["user"]["isAdmin"])
        role = ClientRole.objects.create(user=self.user, client=self.app, is_admin=True)
        self.assertTrue(self.api("game/introspect", body).json()["user"]["isAdmin"])
        self.assertTrue(
            self.api("web/introspect", {"accessToken": self.token.token}).json()["user"]["isAdmin"]
        )
        role.delete()
        self.assertFalse(
            self.api("web/introspect", {"accessToken": self.token.token}).json()["user"]["isAdmin"]
        )
        self.access.accept_legacy = False
        self.access.save()
        self.assertEqual(self.api(client_id="fanmade", key="a" * 64).status_code, 401)
        self.assertEqual(self.api("game/introspect", body).status_code, 200)

    def test_permissions_do_not_follow_application_ownership_or_other_apps(self):
        self.app.user = self.user
        self.app.save()
        other = Application.objects.create(
            name="Other",
            client_id="other",
            client_secret="d" * 64,
            client_type="confidential",
            authorization_grant_type="client-credentials",
        )
        self.assertEqual(self.api(client_id="other", key="d" * 64).status_code, 401)
        ApplicationAccess.objects.create(application=other, allow_web=True)
        self.assertEqual(self.api(client_id="other", key="d" * 64).status_code, 403)
        self.assertEqual(
            self.api("web/introspect", {"accessToken": self.token.token}, "other", "d" * 64).status_code, 401
        )
        ClientRole.objects.create(user=self.user, client=other, is_admin=True)
        self.assertFalse(
            self.api("web/introspect", {"accessToken": self.token.token}).json()["user"]["isAdmin"]
        )
        self.access.allow_game = False
        self.access.save()
        self.assertEqual(self.api("game/login", {}).status_code, 403)
        self.app.client_type = "public"
        self.app.save()
        self.assertEqual(self.api().status_code, 401)
        self.assertEqual(self.api(client_id="fanmade", key="a" * 64).status_code, 401)

    def test_disable_blocks_oauth_userinfo_and_both_service_credentials(self):
        self.access.enabled = False
        self.access.save()
        self.assertEqual(self.api().status_code, 401)
        self.assertEqual(self.api(client_id="fanmade", key="a" * 64).status_code, 401)
        response = self.client.get("/o/userinfo/", HTTP_AUTHORIZATION="Bearer " + self.token.token)
        self.assertEqual(response.status_code, 401)
        self.client.force_login(self.user)
        response = self.client.get(
            "/o/authorize/",
            {
                "client_id": self.app.client_id,
                "response_type": "code",
                "redirect_uri": self.app.redirect_uris,
                "scope": "openid profile",
                "code_challenge": "x" * 43,
                "code_challenge_method": "S256",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_application_id_never_falls_back_to_another_legacy_identity(self):
        Application.objects.create(
            client_id="fanmade",
            name="Collision",
            client_secret="z" * 64,
            client_type="confidential",
            authorization_grant_type="client-credentials",
        )
        self.assertEqual(self.api(client_id="fanmade", key="a" * 64).status_code, 401)
        self.assertEqual(self.api().status_code, 200)
