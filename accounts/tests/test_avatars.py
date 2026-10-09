import io

from django.conf import settings
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from oauth2_provider.models import Application
from PIL import Image

from accounts.models import ApplicationAccess, User


def image_bytes(fmt, size=(800, 600), mode="RGB", **kwargs):
    out = io.BytesIO()
    color = {"RGBA": (200, 80, 40, 128), "L": 90}.get(mode, (200, 80, 40))
    Image.new(mode, size, color).save(out, fmt, **kwargs)
    return out.getvalue()


class AvatarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", nickname="Player", password="Testing!4279")
        self.client.force_login(self.user)
        self.store = storages["avatars"]

    def upload(self, name, data, content_type="application/octet-stream"):
        return self.client.post(
            "/profile/avatar/",
            {"action": "upload", "avatar": SimpleUploadedFile(name, data, content_type)},
        )

    def test_each_accepted_format_is_stored_as_square_webp_under_avatars(self):
        for name, data in [
            ("a.jpg", image_bytes("JPEG")),
            ("b.JPEG", image_bytes("JPEG", (300, 900))),
            ("c.png", image_bytes("PNG", mode="RGBA")),
            ("d.webp", image_bytes("WEBP", (100, 100))),
        ]:
            with self.subTest(name=name):
                self.assertRedirects(self.upload(name, data), "/profile/", fetch_redirect_response=False)
                self.user.refresh_from_db()
                self.assertRegex(self.user.avatar, rf"^avatars/{self.user.pk}/[0-9a-f]{{32}}\.webp$")
                with self.store.open(self.user.avatar) as file, Image.open(file) as stored:
                    self.assertEqual(stored.format, "WEBP")
                    self.assertEqual(stored.width, stored.height)
                    self.assertLessEqual(stored.width, 512)
                    if name == "c.png":
                        self.assertEqual(stored.mode, "RGBA")

    def test_replacing_and_removing_delete_previous_objects(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.upload("a.png", image_bytes("PNG"))
        self.user.refresh_from_db()
        first = self.user.avatar
        with self.captureOnCommitCallbacks(execute=True):
            self.upload("b.png", image_bytes("PNG", (500, 500)))
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.avatar, first)
        self.assertFalse(self.store.exists(first))
        second = self.user.avatar
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/profile/avatar/", {"action": "remove"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar, "")
        self.assertFalse(self.store.exists(second))

    def test_rejects_other_formats_and_mismatched_content(self):
        cases = [
            ("a.gif", image_bytes("GIF")),
            ("a.bmp", image_bytes("BMP")),
            ("a.png", image_bytes("JPEG")),  # extension and content disagree
            ("a.jpg", b"not an image"),
            ("noext", image_bytes("PNG")),
        ]
        for name, data in cases:
            with self.subTest(name=name):
                response = self.upload(name, data)
                self.assertEqual(response.status_code, 400)
                self.user.refresh_from_db()
                self.assertEqual(self.user.avatar, "")

    @override_settings(AVATAR_MAX_UPLOAD_BYTES=1000)
    def test_rejects_oversized_uploads(self):
        response = self.upload("a.png", image_bytes("PNG", (1500, 1500)) + b"\0" * 70000)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "5 MB", status_code=400)

    def test_rejects_huge_dimensions(self):
        response = self.upload("a.png", image_bytes("PNG", (8000, 6000), mode="L"))
        self.assertEqual(response.status_code, 400)

    def test_served_publicly_with_immutable_cache_and_exposed_in_apis(self):
        self.upload("a.png", image_bytes("PNG"))
        self.user.refresh_from_db()
        url = self.user.avatar_url
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertIn("immutable", response["Cache-Control"])
        self.assertEqual(
            self.client.get(url.replace(url.split("/")[-1], "0" * 32 + ".webp")).status_code, 404
        )

        app = Application.objects.create(
            name="Fanmade",
            client_id="fanmade-web",
            client_secret="c" * 64,
            client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="http://localhost:9001/callback",
        )
        ApplicationAccess.objects.create(application=app, allow_profiles=True)
        lookup = self.client.post(
            "/internal/v1/users/lookup",
            {"userIds": [self.user.pk]},
            content_type="application/json",
            HTTP_X_SERVICE_ID="fanmade-web",
            HTTP_X_SERVICE_KEY="c" * 64,
        )
        self.assertTrue(lookup.json()["users"][0]["avatarUrl"].endswith(url))

        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_profile_pages_render_avatar_image(self):
        self.upload("a.png", image_bytes("PNG"))
        self.user.refresh_from_db()
        for path in ["/", "/profile/"]:
            self.assertContains(self.client.get(path), f'src="{self.user.avatar_url}"')

    def test_upload_requires_login(self):
        self.client.logout()
        response = self.upload("a.png", image_bytes("PNG"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(pk=self.user.pk).avatar, "")

    def test_oidc_userinfo_returns_picture_only_when_set(self):
        from datetime import timedelta

        from django.utils import timezone
        from oauth2_provider.models import AccessToken

        app = Application.objects.create(
            name="Fanmade",
            client_id="fanmade-web",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="http://localhost:9001/callback",
            algorithm="RS256",
        )
        AccessToken.objects.create(
            application=app,
            user=self.user,
            token="userinfo-token",
            scope="openid profile",
            expires=timezone.now() + timedelta(hours=1),
        )
        self.client.logout()

        def userinfo():
            return self.client.get("/o/userinfo/", HTTP_AUTHORIZATION="Bearer userinfo-token").json()

        self.assertNotIn("picture", userinfo())
        self.client.force_login(self.user)
        self.upload("a.png", image_bytes("PNG"))
        self.client.logout()
        self.user.refresh_from_db()
        self.assertEqual(userinfo()["picture"], settings.SSO_BASE_URL + self.user.avatar_url)

    @override_settings(AVATAR_CDN_BASE_URL="https://cdn.example/sso/avatars")
    def test_cdn_url_claim_csp_and_legacy_route(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from django.urls import reverse

        from accounts.api import avatar_url
        from accounts.oidc import AccountValidator

        self.upload("cdn.png", image_bytes("PNG"))
        self.user.refresh_from_db()
        digest = self.user.avatar.rsplit("/", 1)[-1].removesuffix(".webp")
        expected = f"https://cdn.example/sso/avatars/{self.user.pk}/{digest}.webp"
        self.assertEqual(self.user.avatar_url, expected)
        self.assertEqual(avatar_url(self.user), expected)
        self.assertEqual(
            AccountValidator().get_additional_claims()["picture"](SimpleNamespace(user=self.user)), expected
        )
        response = self.client.get("/profile/")
        self.assertContains(response, expected)
        self.assertIn("img-src 'self' data: https://cdn.example;", response["Content-Security-Policy"])
        legacy = reverse("avatar", args=[self.user.pk, digest])
        self.client.logout()
        with patch("accounts.avatars.storage", side_effect=AssertionError("CDN must serve bytes")):
            response = self.client.get(legacy)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], expected)
            self.assertEqual(response["Cache-Control"], "no-store")
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get(legacy).status_code, 404)
        self.user.avatar = ""
        self.assertEqual(self.user.absolute_avatar_url, "")
