from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import Application

from accounts.models import ApplicationAccess, GameAccount, GameSession, User
from accounts.security import digest


class GameAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", nickname="Player", password="Testing!4279")
        # Migration 0007 created the public OurTaikoPlay Application.
        self.game = Application.objects.get(client_id="OurTaikoPlay")
        self.fanmade = Application.objects.create(
            name="Fanmade",
            client_id="fanmade",
            client_secret="c" * 64,
            client_type="confidential",
            authorization_grant_type="authorization-code",
        )
        self.fanmade_access = ApplicationAccess.objects.create(
            application=self.fanmade, allow_game=True, allow_account_tokens=True
        )

    def login(self, **body):
        body = {"username": "player", "password": "Testing!4279", **body}
        return self.client.post("/api/v1/game/account/login", body, content_type="application/json")

    def token(self):
        response = self.login(deviceName="Desk PC")
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def session(self, token):
        return self.client.get("/api/v1/game/account/session", HTTP_AUTHORIZATION="Bearer " + token)

    def trade(self, token, service="fanmade"):
        return self.client.post(
            "/api/v1/game/tokens",
            {"service": service},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )

    def service(self, path, body):
        return self.client.post(
            "/internal/v1/game/" + path,
            body,
            content_type="application/json",
            HTTP_X_SERVICE_ID="fanmade",
            HTTP_X_SERVICE_KEY="c" * 64,
        )

    def test_game_client_is_public_and_cannot_use_oauth(self):
        self.assertEqual(self.game.client_type, "public")
        self.assertEqual(self.game.redirect_uris, "")
        self.assertTrue(ApplicationAccess.objects.get(application=self.game).enabled)

    def test_login_returns_a_sliding_token_and_never_stores_it(self):
        response = self.login(deviceName="Desk\nPC")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        body = response.json()
        self.assertRegex(body["token"], r"^[0-9a-f]{64}$")
        self.assertEqual(body["user"]["id"], self.user.pk)
        self.assertEqual(body["user"]["nickname"], "Player")
        account = GameAccount.objects.get()
        self.assertEqual(account.token_hash, digest(body["token"]))
        self.assertEqual(account.client, self.game)
        self.assertEqual(account.device_name, "DeskPC")
        self.assertAlmostEqual(
            account.expires_at, timezone.now() + timedelta(days=30), delta=timedelta(minutes=1)
        )

    def test_bad_credentials_shapes_and_browsers_are_refused(self):
        self.assertEqual(self.login(password="wrong").status_code, 401)
        self.assertEqual(self.login(extra=1).status_code, 400)
        self.assertEqual(
            self.client.post("/api/v1/game/account/login", "x", content_type="text/plain").status_code, 415
        )
        browser = self.client.post(
            "/api/v1/game/account/login",
            {"username": "player", "password": "Testing!4279"},
            content_type="application/json",
            HTTP_ORIGIN="https://evil.test",
        )
        self.assertEqual(browser.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/game/account/login").status_code, 405)
        self.assertFalse(GameAccount.objects.exists())

    def test_login_is_rate_limited_like_other_sign_ins(self):
        for _ in range(15):
            self.login(password="wrong")
        self.assertEqual(self.login().status_code, 429)

    def test_session_slides_the_expiry_forward(self):
        token = self.token()
        stale = timezone.now() - timedelta(days=20)
        GameAccount.objects.update(last_used_at=stale, expires_at=stale + timedelta(days=30))
        response = self.session(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "player")
        account = GameAccount.objects.get()
        self.assertAlmostEqual(account.last_used_at, timezone.now(), delta=timedelta(minutes=1))
        self.assertAlmostEqual(
            account.expires_at, timezone.now() + timedelta(days=30), delta=timedelta(minutes=1)
        )

    def test_expired_and_malformed_tokens_are_401(self):
        token = self.token()
        GameAccount.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.session(token).status_code, 401)
        self.assertEqual(self.trade(token).status_code, 401)
        self.assertEqual(self.session("nothex").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/game/account/session").status_code, 401)

    def test_trade_gives_an_ordinary_session_of_that_service_only(self):
        token = self.token()
        response = self.trade(token)
        self.assertEqual(response.status_code, 200)
        traded = response.json()
        self.assertEqual(traded["expiresIn"], 7 * 86400)
        self.assertEqual(traded["user"]["id"], self.user.pk)
        self.assertFalse(traded["user"]["isAdmin"])
        session = GameSession.objects.get()
        self.assertEqual(session.client, self.fanmade)
        # The business service checks it exactly as one from its own game/login.
        introspect = self.service("introspect", {"accessToken": traded["accessToken"]})
        self.assertEqual(introspect.status_code, 200)
        self.assertEqual(introspect.json()["user"]["id"], self.user.pk)
        # The account token itself is no game session of any service.
        self.assertEqual(self.service("introspect", {"accessToken": token}).status_code, 401)

    def test_trade_needs_the_service_to_opt_in(self):
        token = self.token()
        self.assertEqual(self.trade(token, "missing").status_code, 403)
        self.assertEqual(self.trade(token, "OurTaikoPlay").status_code, 403)
        self.fanmade_access.allow_account_tokens = False
        self.fanmade_access.save()
        self.assertEqual(self.trade(token).status_code, 403)
        self.fanmade_access.allow_account_tokens, self.fanmade_access.allow_game = True, False
        self.fanmade_access.save()
        self.assertEqual(self.trade(token).status_code, 403)
        self.fanmade_access.allow_game, self.fanmade_access.enabled = True, False
        self.fanmade_access.save()
        self.assertEqual(self.trade(token).status_code, 403)
        bad = self.client.post(
            "/api/v1/game/tokens",
            {"service": "fanmade", "x": 1},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(GameSession.objects.exists())

    def test_password_change_revokes_accounts_and_traded_sessions(self):
        token = self.token()
        traded = self.trade(token).json()["accessToken"]
        self.user.set_password("Another!9531")
        self.user.save()
        self.assertEqual(self.session(token).status_code, 401)
        self.assertEqual(self.service("introspect", {"accessToken": traded}).status_code, 401)
        self.assertFalse(GameAccount.objects.exists())

    def test_disabling_the_game_client_stops_game_accounts(self):
        token = self.token()
        access = ApplicationAccess.objects.get(application=self.game)
        access.enabled = False
        access.save()
        self.assertEqual(self.session(token).status_code, 401)
        self.assertEqual(self.login().status_code, 403)

    def test_logout_removes_only_that_device(self):
        first, second = self.token(), self.token()
        response = self.client.post(
            "/api/v1/game/account/logout",
            {},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + first,
        )
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.session(first).status_code, 401)
        self.assertEqual(self.session(second).status_code, 200)

    def test_sessions_page_lists_and_revokes_devices(self):
        token = self.token()
        self.client.force_login(self.user)
        page = self.client.get("/sessions/")
        self.assertContains(page, "Desk PC")
        response = self.client.post(
            "/sessions/revoke/", {"kind": "account", "key": GameAccount.objects.get().pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.session(token).status_code, 401)

    @override_settings(GAME_ACCOUNT_IDLE_DAYS=1)
    def test_cleanup_removes_expired_accounts(self):
        self.token()
        GameAccount.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        call_command("cleanup_auth", stdout=open("/dev/null", "w"))
        self.assertFalse(GameAccount.objects.exists())
