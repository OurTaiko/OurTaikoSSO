"""Narrow two-sided unlink checks; all Viewer requests are mocked."""
from unittest import mock

from django.test import TestCase, override_settings
from oauth2_provider.models import Application

from accounts.models import ExternalLink, User
from accounts.sakura import SakuraServiceError


@override_settings(
    SAKURA_REVERSE_ENABLED=True,
    SAKURA_REVERSE_SERVICE_ID="OurTaiko",
    SAKURA_REVERSE_SERVICE_KEY="synthetic-test-service-key-not-a-production-secret",
)
class SakuraAdminUnlinkTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", nickname="Root", email="root@example.test", password="Testing!4279"
        )
        self.user = User.objects.create_user(
            username="player", nickname="Player", email="player@example.test", password="Testing!4279"
        )
        self.app = Application.objects.create(
            name="Sakura", client_id="Sakura", client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="https://viewer.sakura-bot.cn/api/account/oidc/ourtaiko/callback",
        )
        self.link = ExternalLink.objects.create(
            application=self.app, user=self.user, external_id="1996075848453"
        )
        self.client.force_login(self.admin)
        self.path = f"/admin/accounts/externallink/{self.link.pk}/delete/"

    @mock.patch("accounts.sakura.unlink_viewer")
    def test_remote_confirmed_before_local_delete(self, unlink):
        unlink.return_value = {"id":self.link.external_id,"externalId":self.user.pk}
        response = self.client.post(self.path, {"post":"yes"})
        self.assertEqual(response.status_code, 302)
        unlink.assert_called_once_with(self.link.external_id, self.user.pk)
        self.assertFalse(ExternalLink.objects.filter(pk=self.link.pk).exists())

    @mock.patch("accounts.sakura.unlink_viewer")
    def test_404_exactly_not_found_allows_local_delete(self, unlink):
        unlink.side_effect = SakuraServiceError(404, "NOT_FOUND")
        self.assertEqual(self.client.post(self.path, {"post":"yes"}).status_code, 302)
        self.assertFalse(ExternalLink.objects.filter(pk=self.link.pk).exists())

    @mock.patch("accounts.sakura.unlink_viewer")
    def test_409_or_unknown_never_deletes_local_binding(self, unlink):
        for error in [SakuraServiceError(409,"ALREADY_LINKED"),SakuraServiceError()]:
            unlink.side_effect=error
            response=self.client.post(self.path, {"post":"yes"})
            self.assertEqual(response.status_code, 403)
            self.assertTrue(ExternalLink.objects.filter(pk=self.link.pk).exists())
