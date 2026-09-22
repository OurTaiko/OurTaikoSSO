from datetime import timedelta

from django.core import mail
from django.test import Client, TestCase
from django.utils import timezone, translation
from oauth2_provider.models import AccessToken, Application

from accounts.models import GameClient, User
from accounts.security import digest


class LanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="langtest", nickname="Test", password="Testing!4279")
        self.app = Application.objects.create(
            name="Language test",
            client_id="language-web",
            client_secret="test-secret",
            client_type="confidential",
            authorization_grant_type="authorization-code",
        )
        self.service = GameClient.objects.create(
            id="language-test",
            name="Test",
            secret_hash=digest("a" * 64),
            web_client_id=self.app.client_id,
        )
        self.token = AccessToken.objects.create(
            user=self.user,
            application=self.app,
            token="language-test-token",
            scope="openid profile",
            expires=timezone.now() + timedelta(hours=1),
        )

    def test_defaults_to_chinese_without_browser_detection(self):
        self.assertEqual(self.user.preferred_language, "zh-hans")
        response = self.client.get("/login/", HTTP_ACCEPT_LANGUAGE="en-US")
        self.assertEqual(response["Content-Language"], "zh-hans")
        self.assertContains(response, "登录账号")

    def test_anonymous_selection_and_authenticated_preference(self):
        response = self.client.post(
            "/language/", {"language": "ja", "next": "/login/?next=/profile/"}, follow=True
        )
        self.assertContains(response, 'lang="ja"')
        self.assertContains(response, "ログイン")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/profile/")["Content-Language"], "zh-hans")
        self.client.post("/language/", {"language": "ko", "next": "/profile/"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_language, "ko")
        other_browser = Client()
        other_browser.force_login(self.user)
        self.assertContains(other_browser.get("/profile/"), "프로필")

    def test_profile_save_persists_language_and_keeps_credentials(self):
        self.client.force_login(self.user)
        old_password = self.user.password
        old_version = self.user.auth_version
        response = self.client.post(
            "/profile/", {"nickname": "Updated", "preferred_language": "en"}, follow=True
        )
        self.assertContains(response, "Profile updated.")
        self.user.refresh_from_db()
        self.assertEqual((self.user.preferred_language, self.user.nickname), ("en", "Updated"))
        self.assertEqual((self.user.password, self.user.auth_version), (old_password, old_version))
        self.assertTrue(AccessToken.objects.filter(pk=self.token.pk).exists())
        self.assertEqual(self.client.get("/profile/")["Content-Language"], "en")

    def test_rejects_invalid_languages_csrf_and_external_redirects(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/language/").status_code, 405)
        self.assertEqual(self.client.post("/language/", {"language": "fr"}).status_code, 400)
        self.client.post("/profile/", {"nickname": "Overwrite", "preferred_language": "fr"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.preferred_language, self.user.nickname), ("zh-hans", "Test"))
        response = self.client.post("/language/", {"language": "en", "next": "https://evil.example/"})
        self.assertEqual(response.url, "/")
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(browser.post("/language/", {"language": "ja"}).status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_language, "en")

    def test_live_private_identity_and_oidc_profile_claim(self):
        self.client.force_login(self.user)
        for language in ["en", "ja", "ko", "zh-hans"]:
            self.client.post("/language/", {"language": language})
            response = self.client.post(
                "/internal/v1/web/introspect",
                {"accessToken": self.token.token},
                content_type="application/json",
                HTTP_X_SERVICE_ID=self.service.id,
                HTTP_X_SERVICE_KEY="a" * 64,
            )
            self.assertEqual(response.json()["user"]["preferredLanguage"], language)
            userinfo = self.client.get("/o/userinfo/", HTTP_AUTHORIZATION="Bearer " + self.token.token)
            self.assertEqual(userinfo.json()["locale"], language)
        self.token.scope = "openid"
        self.token.save(update_fields=["scope"])
        self.assertNotIn(
            "locale", self.client.get("/o/userinfo/", HTTP_AUTHORIZATION="Bearer " + self.token.token).json()
        )
        public = self.client.post(
            "/internal/v1/users/lookup",
            {"userIds": [self.user.pk]},
            content_type="application/json",
            HTTP_X_SERVICE_ID=self.service.id,
            HTTP_X_SERVICE_KEY="a" * 64,
        ).json()
        self.assertNotIn("preferredLanguage", public["users"][0])

    def test_every_page_renders_in_all_four_languages(self):
        self.client.force_login(self.user)
        for language, heading in [
            ("zh-hans", "账号概览"),
            ("en", "Account overview"),
            ("ja", "アカウント概要"),
            ("ko", "계정 개요"),
        ]:
            User.objects.filter(pk=self.user.pk).update(preferred_language=language)
            for path in [
                "/",
                "/profile/",
                "/security/",
                "/sessions/",
                "/connections/",
                "/login/",
                "/register/",
                "/password/reset/",
            ]:
                with self.subTest(language=language, path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Language"], language)
            self.assertContains(self.client.get("/"), heading)

    def test_registration_keeps_selected_language_and_translates_mail(self):
        self.client.post("/language/", {"language": "ja"})
        response = self.client.post(
            "/register/",
            {
                "username": "newlang",
                "nickname": "New",
                "email": "new@example.test",
                "password1": "NewLanguage!4268",
                "password2": "NewLanguage!4268",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.get(username="newlang").preferred_language, "ja")
        self.assertIn("メールアドレス", mail.outbox[0].subject)
        self.assertIn("/verify/", mail.outbox[0].body)
        # Per-request activation must not leak to background tasks or the next request.
        with translation.override("en"):
            self.client.get("/login/")
            self.assertEqual(translation.get_language(), "en")

    def test_password_reset_mail_uses_recipient_preference(self):
        self.user.email = "reset@example.test"
        self.user.email_verified = True
        self.user.preferred_language = "ko"
        self.user.save(update_fields=["email", "email_verified", "preferred_language"])
        self.client.post("/language/", {"language": "en"})
        self.client.post("/password/reset/", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("비밀번호 재설정", mail.outbox[0].subject)
        self.assertIn("30분", mail.outbox[0].body)
        self.assertIn("/password/reset/", mail.outbox[0].body)
