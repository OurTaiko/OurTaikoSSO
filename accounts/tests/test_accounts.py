import base64
import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import bcrypt
from django.conf import settings
from django.contrib.auth import authenticate
from django.core import mail
from django.test import Client, TestCase
from django.utils import timezone
from jwcrypto import jwk, jwt
from oauth2_provider.models import AccessToken, Application

from accounts.models import ApplicationAccess, ClientRole, GameSession, User
from accounts.security import digest

PASSWORD = "Rhythm!Dev4279"


class AccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="player1",
            nickname="小咚",
            email="player@example.test",
            email_verified=True,
            password=PASSWORD,
        )
        self.fanmade = self.service("fanmade", "a" * 64)
        self.ese = self.service("ese", "b" * 64)

    def service(self, client_id, secret):
        app = Application.objects.create(client_id=client_id, name=client_id, client_secret=secret,
            client_type="confidential", authorization_grant_type="authorization-code", algorithm="RS256")
        ApplicationAccess.objects.create(application=app, allow_game=True, allow_profiles=True, allow_web=True)
        return app

    def api(self, path, body, service="fanmade", key="a" * 64, **headers):
        return self.client.post(
            "/internal/v1/" + path,
            body,
            content_type="application/json",
            HTTP_X_SERVICE_ID=service,
            HTTP_X_SERVICE_KEY=key,
            **headers,
        )

    def game_login(self):
        response = self.api("game/login", {"username": "player1", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        return response.json()["accessToken"]

    def test_game_wire_contract_and_private_storage(self):
        response = self.api("game/login", {"username": "player1", "password": PASSWORD})
        body = response.json()
        self.assertEqual(set(body), {"user", "accessToken", "expiresIn"})
        self.assertEqual(body["user"]["id"], self.user.id)
        self.assertRegex(body["accessToken"], r"^[0-9a-f]{64}$")
        self.assertEqual(body["expiresIn"], 604800)
        self.assertNotEqual(GameSession.objects.get().token_hash, body["accessToken"])
        self.assertEqual(GameSession.objects.get().token_hash, digest(body["accessToken"]))
        self.assertFalse(body["user"]["isAdmin"])

    def test_game_service_auth_audience_and_cookie_isolation(self):
        self.assertEqual(self.api("game/login", {}, key="bad").status_code, 401)
        self.assertEqual(self.api("game/login", {}, HTTP_ORIGIN="http://evil.example").status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post("/internal/v1/game/login", {}, content_type="application/json").status_code, 401
        )
        token = self.game_login()
        self.assertEqual(
            self.api("game/introspect", {"accessToken": token}, service="ese", key="b" * 64).status_code, 401
        )
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 200)
        ApplicationAccess.objects.filter(application=self.fanmade).update(enabled=False)
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)

    def test_admin_privileges_are_per_application(self):
        ClientRole.objects.create(user=self.user, client=self.fanmade, is_admin=True)
        self.user.is_superuser = True
        self.user.save()
        credentials = {"username": "player1", "password": PASSWORD}
        self.assertTrue(self.api("game/login", credentials).json()["user"]["isAdmin"])
        self.assertFalse(
            self.api("game/login", credentials, service="ese", key="b" * 64).json()["user"]["isAdmin"]
        )

    def test_expiry_revoke_and_disabled_user(self):
        token = self.game_login()
        GameSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)
        token = self.game_login()
        self.assertEqual(self.api("game/revoke", {"accessToken": token}).status_code, 200)
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)
        token = self.game_login()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)
        self.assertEqual(
            self.api("game/login", {"username": "player1", "password": PASSWORD}).status_code, 401
        )

    def test_public_profiles_are_minimal_and_current(self):
        self.user.nickname = "新名字"
        self.user.save()
        response = self.api("users/lookup", {"userIds": [self.user.pk]})
        self.assertEqual(response.json(), {"users": [{"id": self.user.pk, "nickname": "新名字", "avatarUrl": ""}]})
        self.assertEqual(self.api("users/lookup", {"userIds": [self.user.pk] * 101}).status_code, 400)
        self.assertEqual(self.api("users/lookup", {"userIds": [17]}).status_code, 400)

    def test_id_lookup_rejects_username_requests(self):
        for body in [{"usernames": ["player1"]}, {"userIds": [self.user.pk], "usernames": ["player1"]}]:
            self.assertEqual(self.api("users/lookup", body).status_code, 400)

    def test_profiles_by_username_are_exact_minimal_and_current(self):
        path = "users/lookup-by-username"
        self.user.nickname = "新的昵称"
        self.user.save()
        result = self.api(path, {"usernames": ["missing", "player1", "player1", "Player1"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"users": [{
            "id": self.user.pk, "username": "player1", "nickname": "新的昵称", "avatarUrl": "",
        }]})
        for body in [
            {"usernames": ["player1"] * 101}, {"usernames": [17]}, {"usernames": [""]},
            {"usernames": [" player1"]}, {"usernames": ["a" * 25]},
            {"usernames": [], "userIds": []}, {"usernames": "player1"},
            {"userIds": [self.user.pk]},
        ]:
            self.assertEqual(self.api(path, body).status_code, 400)
        self.assertEqual(self.api(path, {"usernames": []}).json(), {"users": []})
        self.assertEqual(self.api(path, {"usernames": ["player1"]}, key="bad").status_code, 401)
        self.assertEqual(self.api(path, {"usernames": ["player1"]}, HTTP_ORIGIN="https://evil.example").status_code, 403)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.api(path, {"usernames": ["player1"]}).json(), {"users": []})
        ApplicationAccess.objects.filter(application=self.fanmade).update(allow_profiles=False)
        self.assertEqual(self.api(path, {"usernames": ["player1"]}).status_code, 403)

    def test_login_rate_limit_shared_with_browser(self):
        for _ in range(15):
            self.assertEqual(
                self.api("game/login", {"username": "player1", "password": "bad"}).status_code, 401
            )
        self.assertEqual(
            self.api("game/login", {"username": "player1", "password": PASSWORD}).status_code, 429
        )
        response = self.client.post("/login/", {"username": "player1", "password": PASSWORD})
        self.assertContains(response, "尝试次数过多")

    def test_browser_csrf_and_safe_redirect(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(
            browser.post("/login/", {"username": "player1", "password": PASSWORD}).status_code, 403
        )
        browser.get("/login/")
        csrf = browser.cookies[settings.CSRF_COOKIE_NAME].value
        response = browser.post(
            "/login/",
            {
                "username": "player1",
                "password": PASSWORD,
                "csrfmiddlewaretoken": csrf,
                "next": "https://evil.example",
            },
        )
        self.assertRedirects(response, "/")
        self.assertEqual(browser.post("/profile/", {"nickname": "bad"}).status_code, 403)
        self.assertEqual(browser.get("/logout/").status_code, 405)

    def test_profile_edit_preserves_id_and_rejects_control_chars(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post("/profile/", {"nickname": "咚咚", "preferred_language": "zh-hans"}).status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nickname, "咚咚")
        self.assertEqual(self.client.post("/profile/", {"nickname": "bad\nname", "preferred_language": "zh-hans"}).status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nickname, "咚咚")

    def test_password_change_revokes_tokens_and_other_browsers(self):
        token = self.game_login()
        other = Client()
        other.force_login(self.user)
        self.client.force_login(self.user)
        app = self.application()
        AccessToken.objects.create(
            user=self.user,
            application=app,
            token="web-token",
            scope="openid",
            expires=timezone.now() + timedelta(hours=1),
        )
        response = self.client.post(
            "/security/",
            {"old_password": PASSWORD, "new_password1": "NewRhythm!6721", "new_password2": "NewRhythm!6721"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)
        self.assertFalse(AccessToken.objects.exists())
        self.assertEqual(other.get("/").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_registration_verification_and_email_uniqueness(self):
        response = self.client.post(
            "/register/",
            {
                "username": "newPlayer",
                "nickname": "新人",
                "email": "NEW@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertContains(response, "去邮箱看看吧")
        user = User.objects.get(username="newPlayer")
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, "new@example.test")
        link = re.search(r"http://[^\s]+", mail.outbox[0].body).group()
        path = urlsplit(link).path
        self.client.get(path)
        user.refresh_from_db()
        self.assertFalse(user.is_active)  # Email scanners cannot activate accounts with GET.
        self.assertEqual(self.client.post(path).status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.email_verified and user.is_active)
        self.assertContains(self.client.post(path), "链接已失效")
        self.assertIsNotNone(authenticate(username="newPlayer", password=PASSWORD))

    def test_password_reset_link_single_use_and_game_revocation(self):
        token = self.game_login()
        self.client.post("/password/reset/", {"email": self.user.email})
        link = re.search(r"http://[^\s]+", mail.outbox[0].body).group()
        response = self.client.get(urlsplit(link).path)
        reset_path = response["Location"]
        self.assertEqual(self.client.get(reset_path).status_code, 200)
        response = self.client.post(
            reset_path, {"new_password1": "ResetRhythm!5217", "new_password2": "ResetRhythm!5217"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)
        self.assertContains(self.client.get(urlsplit(link).path), "链接已失效")

    def test_user_cannot_revoke_another_users_session(self):
        token = self.game_login()
        stranger = User.objects.create_user(username="stranger", nickname="路人", password=PASSWORD)
        self.client.force_login(stranger)
        self.client.post("/sessions/revoke/", {"kind": "game", "key": digest(token)})
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 200)

    def test_legacy_go_bcrypt_password_preserves_user_id(self):
        before = self.user.pk
        original_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
        self.user.password = "bcrypt$" + original_hash
        self.user.save()
        user = authenticate(username="player1", password=PASSWORD)
        self.assertIsNotNone(user)
        self.assertEqual(user.pk, before)
        self.assertNotEqual(
            user.password, "bcrypt$" + original_hash
        )  # Upgraded after successful verification.

    def test_long_password_does_not_crash_legacy_bcrypt(self):
        self.user.password = "bcrypt$" + bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
        self.user.save()
        self.assertIsNone(authenticate(username="player1", password="长" * 40))
        self.assertEqual(
            self.api("game/login", {"username": "player1", "password": "x" * 100}).status_code, 401
        )

    def test_unverified_email_cannot_reset_existing_account(self):
        self.user.email_verified = False
        self.user.save()
        self.client.post("/password/reset/", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_from_logged_in_browser(self):
        self.client.force_login(self.user)
        self.client.post("/password/reset/", {"email": self.user.email})
        link = re.search(r"http://[^\s]+", mail.outbox[0].body).group()
        response = self.client.get(urlsplit(link).path)
        response = self.client.post(
            response["Location"],
            {
                "new_password1": "ChangedAgain!8591",
                "new_password2": "ChangedAgain!8591",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertIsNotNone(authenticate(username="player1", password="ChangedAgain!8591"))

    def test_stale_last_login_update_does_not_revoke_new_tokens(self):
        stale_user = User.objects.get(pk=self.user.pk)
        self.user.set_password("NewerPassword!8279")
        self.user.save()
        response = self.api("game/login", {"username": self.user.username, "password": "NewerPassword!8279"})
        token = response.json()["accessToken"]
        stale_user.last_login = timezone.now()
        stale_user.save(update_fields=["last_login"])
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 200)

    def test_revoke_all_logs_out_browser_and_game(self):
        token = self.game_login()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post("/sessions/revoke-all/").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.api("game/introspect", {"accessToken": token}).status_code, 401)

    def application(self):
        return Application.objects.create(
            name="Test RP",
            client_id="test-client",
            client_secret="test-secret",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            algorithm="RS256",
            redirect_uris="http://localhost:9001/callback",
        )

    def authorize(self, verifier="x" * 64):
        self.application()
        self.client.force_login(self.user)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        data = {
            "client_id": "test-client",
            "response_type": "code",
            "redirect_uri": "http://localhost:9001/callback",
            "scope": "openid profile",
            "state": "test-state",
            "nonce": "test-nonce",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        self.assertEqual(self.client.get("/o/authorize/", data).status_code, 200)
        response = self.client.post("/o/authorize/", {**data, "allow": "true"})
        self.assertEqual(response.status_code, 302)
        values = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(values["state"], ["test-state"])
        return values["code"][0]

    def exchange(self, code, verifier):
        basic = base64.b64encode(b"test-client:test-secret").decode()
        return self.client.post(
            "/o/token/",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://localhost:9001/callback",
                "code_verifier": verifier,
            },
            HTTP_AUTHORIZATION="Basic " + basic,
        )

    def test_oidc_authorization_code_pkce_signature_and_userinfo(self):
        code = self.authorize()
        response = self.exchange(code, "x" * 64)
        self.assertEqual(response.status_code, 200, response.content)
        tokens = response.json()
        key = jwk.JWK.from_pem(settings.OAUTH2_PROVIDER["OIDC_RSA_PRIVATE_KEY"].encode())
        signed = jwt.JWT(
            jwt=tokens["id_token"],
            key=jwk.JWK.from_json(key.export_public()),
            algs=["RS256"],
            check_claims={
                "iss": settings.SSO_BASE_URL,
                "aud": "test-client",
                "nonce": "test-nonce",
                "exp": None,
            },
        )
        self.assertIn(self.user.pk, signed.claims)
        response = self.client.get("/o/userinfo/", HTTP_AUTHORIZATION="Bearer " + tokens["access_token"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["nickname"], "小咚")
        self.assertEqual(response.json()["sub"], self.user.pk)
        self.assertEqual(self.exchange(code, "x" * 64).status_code, 400)

    def test_oidc_wrong_pkce_and_redirect_are_rejected(self):
        code = self.authorize()
        self.assertEqual(self.exchange(code, "wrong" * 15).status_code, 400)
        response = self.client.get(
            "/o/authorize/",
            {
                "client_id": "test-client",
                "response_type": "code",
                "redirect_uri": "http://evil.example/callback",
                "scope": "openid",
            },
        )
        self.assertNotEqual(response.status_code, 302)

    def test_discovery_and_no_public_client_registration(self):
        response = self.client.get("/.well-known/openid-configuration")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["issuer"], settings.SSO_BASE_URL)
        self.assertEqual(self.client.get("/o/applications/register/").status_code, 404)

    def test_all_account_pages_render(self):
        self.client.force_login(self.user)
        for path in ["/", "/profile/", "/security/", "/sessions/", "/connections/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_web_bridge_is_bound_to_application_and_revoked_on_password_change(self):
        application = self.fanmade
        token = AccessToken.objects.create(
            user=self.user,
            application=application,
            token="web-access-token",
            scope="openid profile",
            expires=timezone.now() + timedelta(hours=1),
        )
        body = {"accessToken": token.token}
        self.assertEqual(self.api("web/introspect", body).json()["user"]["nickname"], "小咚")
        self.assertEqual(self.api("web/introspect", body, service="ese", key="b" * 64).status_code, 401)
        self.assertEqual(self.api("game/introspect", body).status_code, 401)
        self.assertEqual(self.api("web/introspect", {"accessToken": self.game_login()}).status_code, 401)
        self.assertEqual(self.api("web/revoke", body, service="ese", key="b" * 64).status_code, 200)
        self.assertTrue(AccessToken.objects.filter(pk=token.pk).exists())
        self.user.nickname = "新昵称"
        self.user.save(update_fields=["nickname"])
        self.assertEqual(self.api("web/introspect", body).json()["user"]["nickname"], "新昵称")
        self.user.set_password("ChangedPassword!9384")
        self.user.save()
        self.assertEqual(self.api("web/introspect", body).status_code, 401)

    def test_web_bridge_revoke_and_expiry(self):
        application = self.fanmade
        token = AccessToken.objects.create(
            user=self.user,
            application=application,
            token="web-access-token",
            scope="openid profile",
            expires=timezone.now() - timedelta(seconds=1),
        )
        body = {"accessToken": token.token}
        self.assertEqual(self.api("web/introspect", body).status_code, 401)
        token.expires = timezone.now() + timedelta(hours=1)
        token.save()
        self.assertEqual(self.api("web/revoke", body).status_code, 200)
        self.assertEqual(self.api("web/introspect", body).status_code, 401)
        self.assertEqual(self.api("web/revoke", body).status_code, 200)

    def test_nickname_search_does_not_search_login_names(self):
        self.assertEqual(self.api("users/search", {"query": "小咚"}).json()["userIds"], [self.user.id])
        self.assertEqual(self.api("users/search", {"query": "player1"}).json()["userIds"], [])
        self.assertEqual(
            self.api("users/search", {"query": "小咚", "after": self.user.id}).json()["userIds"], []
        )
        self.assertEqual(self.api("users/search", {"query": ""}).status_code, 400)

    def test_consent_csp_allows_only_registered_callback_origin(self):
        app = self.application()
        self.client.force_login(self.user)
        params = {"client_id": app.client_id, "redirect_uri": "http://localhost:9001/callback"}
        response = self.client.get("/o/authorize/", params)
        self.assertIn("form-action 'self' http://localhost:9001", response["Content-Security-Policy"])
        params["redirect_uri"] = "https://evil.example/callback"
        response = self.client.get("/o/authorize/", params)
        self.assertNotIn("evil.example", response["Content-Security-Policy"])
        self.assertTrue(response["Content-Security-Policy"].endswith("form-action 'self'"))
