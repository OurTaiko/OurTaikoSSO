from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.test import Client, TestCase

from accounts.models import GameClient, User
from accounts.security import digest


class ClientAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="service-admin", password="test-admin-password")
        self.client.force_login(self.admin)
        self.add_url = "/admin/accounts/gameclient/add/"
        self.reset_url = "/admin/accounts/gameclient/ese/reset-secret/"
        self.change_url = "/admin/accounts/gameclient/ese/change/"

    def create_service(self):
        response = self.client.post(self.add_url, {"id": "ese", "name": "ESE", "enabled": "on"})
        self.assertEqual(response.status_code, 200)
        secret = response.context_data["service_secret"]
        self.assertRegex(secret, r"^[0-9a-f]{64}$")
        self.assertEqual(GameClient.objects.get(pk="ese").secret_hash, digest(secret))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(secret, str(dict(self.client.session)))
        self.assertNotIn(secret, str(list(LogEntry.objects.values())))
        return secret

    def test_create_and_edit_preserve_secret(self):
        secret = self.create_service()
        response = self.client.get(self.change_url)
        self.assertNotContains(response, secret)
        response = self.client.post(self.change_url, {"id": "other", "name": "Renamed", "enabled": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GameClient.objects.get(pk="ese").secret_hash, digest(secret))
        self.assertFalse(GameClient.objects.filter(pk="other").exists())
        duplicate = self.client.post(self.add_url, {"id": "ese", "name": "Duplicate"})
        self.assertNotContains(duplicate, secret)
        self.assertEqual(GameClient.objects.get(pk="ese").secret_hash, digest(secret))

    def lookup(self, secret):
        return self.client.post("/internal/v1/users/lookup", {"userIds": []}, content_type="application/json",
                                HTTP_X_SERVICE_ID="ese", HTTP_X_SERVICE_KEY=secret)

    def test_rotate_once_and_authenticate(self):
        old = self.create_service()
        self.assertEqual(self.lookup(old).status_code, 200)
        self.assertNotContains(self.client.get(self.reset_url), old)
        self.assertEqual(GameClient.objects.get(pk="ese").secret_hash, digest(old))
        response = self.client.post(self.reset_url)
        new = response.context_data["service_secret"]
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
        self.assertEqual(csrf_client.post(self.add_url, {"id": "blocked", "name": "blocked"}).status_code, 403)
        viewer = User.objects.create_user(username="viewer", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_gameclient"))
        self.client.force_login(viewer)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.reset_url).status_code, 403)
        self.assertEqual(self.client.post(self.add_url, {"id": "blocked", "name": "blocked"}).status_code, 403)
        self.assertEqual(GameClient.objects.get(pk="ese").secret_hash, digest(old))
        self.client.logout()
        self.assertEqual(self.client.post(self.reset_url).status_code, 302)
