from django.contrib.admin.models import LogEntry
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from oauth2_provider.models import Application

from accounts.models import ApplicationAccess, User


class ClientAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="service-admin", password="test-admin-password")
        self.client.force_login(self.admin)
        self.add_url = "/admin/oauth2_provider/application/add/"
        self.data = {
            "client_id": "ese",
            "name": "ESE",
            "enabled": "on",
            "allow_profiles": "on",
            "client_type": "confidential",
            "authorization_grant_type": "client-credentials",
            "algorithm": "RS256",
        }

    def create_service(self):
        response = self.client.post(self.add_url, self.data)
        self.assertEqual(response.status_code, 200)
        secret = response.context_data["application_secret"]
        self.assertRegex(secret, r"^[0-9a-f]{64}$")
        self.assertTrue(check_password(secret, Application.objects.get(client_id="ese").client_secret))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(secret, str(dict(self.client.session)))
        self.assertNotIn(secret, str(list(LogEntry.objects.values())))
        self.app = Application.objects.get(client_id="ese")
        self.reset_url = f"/admin/oauth2_provider/application/{self.app.pk}/reset-secret/"
        self.change_url = f"/admin/oauth2_provider/application/{self.app.pk}/change/"
        return secret

    def test_create_and_edit_preserve_secret(self):
        secret = self.create_service()
        response = self.client.get(self.change_url)
        self.assertNotContains(response, secret)
        response = self.client.post(self.change_url, {**self.data, "client_id": "other", "name": "Renamed"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(check_password(secret, Application.objects.get(client_id="ese").client_secret))
        self.assertFalse(Application.objects.filter(client_id="other").exists())
        duplicate = self.client.post(self.add_url, self.data)
        self.assertNotContains(duplicate, secret)
        self.assertTrue(check_password(secret, Application.objects.get(client_id="ese").client_secret))

    def lookup(self, secret):
        return self.client.post(
            "/internal/v1/users/lookup",
            {"userIds": []},
            content_type="application/json",
            HTTP_X_SERVICE_ID="ese",
            HTTP_X_SERVICE_KEY=secret,
        )

    def test_rotate_once_and_authenticate(self):
        old = self.create_service()
        self.assertEqual(self.lookup(old).status_code, 200)
        self.assertNotContains(self.client.get(self.reset_url), old)
        self.assertTrue(check_password(old, Application.objects.get(client_id="ese").client_secret))
        response = self.client.post(self.reset_url)
        new = response.context_data["application_secret"]
        self.assertNotEqual(new, old)
        self.assertEqual(self.lookup(old).status_code, 401)
        self.assertEqual(self.lookup(new).status_code, 200)
        self.assertNotContains(self.client.get(self.reset_url), new)
        self.assertNotIn(new, str(dict(self.client.session)))
        self.assertNotIn(new, str(list(LogEntry.objects.values())))

    def test_permissions_and_csrf(self):
        old = self.create_service()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.reset_url).status_code, 403)
        self.assertEqual(
            csrf_client.post(self.add_url, {"id": "blocked", "name": "blocked"}).status_code, 403
        )
        viewer = User.objects.create_user(username="viewer", is_staff=True)
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_application", content_type__app_label="oauth2_provider")
        )
        self.client.force_login(viewer)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.reset_url).status_code, 403)
        self.assertEqual(
            self.client.post(self.add_url, {"id": "blocked", "name": "blocked"}).status_code, 403
        )
        self.assertTrue(check_password(old, Application.objects.get(client_id="ese").client_secret))
        self.client.logout()
        self.assertEqual(self.client.post(self.reset_url).status_code, 302)

    def test_admin_is_single_application_and_public_cannot_gain_internal_access(self):
        index = self.client.get("/admin/")
        self.assertNotContains(index, "/accounts/gameclient/")
        response = self.client.post(self.add_url, {**self.data, "client_type": "public"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Application.objects.exists())
        response = self.client.post(self.add_url, {**self.data, "accept_legacy": "on"})
        self.assertFalse(Application.objects.exists())

    def test_rotation_disables_legacy_and_edit_keeps_oauth_config(self):
        from accounts.security import digest

        old = self.create_service()
        ApplicationAccess.objects.filter(application=self.app).update(
            accept_legacy=True, legacy_service_id="old-ese", legacy_secret_hash=digest("a" * 64)
        )
        response = self.client.post(self.reset_url)
        new = response.context_data["application_secret"]
        self.assertFalse(ApplicationAccess.objects.get(application=self.app).accept_legacy)
        self.assertEqual(self.lookup(old).status_code, 401)
        self.assertEqual(self.lookup(new).status_code, 200)
