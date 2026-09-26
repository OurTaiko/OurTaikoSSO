from importlib import import_module

from django.apps import apps
from django.test import TestCase
from oauth2_provider.models import Application

from accounts.models import GameClient


class ApplicationMigrationPreflightTests(TestCase):
    def setUp(self):
        self.plan = import_module("accounts.migrations.0004_unified_applications").migration_plan
        self.app = Application.objects.create(
            client_id="website",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            client_secret="test-secret",
        )

    def test_explicit_binding_and_service_only_plan_are_read_only(self):
        GameClient.objects.create(id="site", name="Site", web_client_id="website", secret_hash="a" * 64)
        GameClient.objects.create(id="standalone", name="Server", secret_hash="b" * 64)
        before = list(Application.objects.values())
        result = self.plan(apps, "default")
        self.assertEqual([app.pk if app else None for _, app in result], [self.app.pk, None])
        self.assertEqual(before, list(Application.objects.values()))

    def test_missing_public_duplicate_and_shadowed_bindings_fail_closed(self):
        service = GameClient.objects.create(
            id="site", name="Site", web_client_id="missing", secret_hash="a" * 64
        )
        with self.assertRaisesRegex(RuntimeError, "missing/public"):
            self.plan(apps, "default")
        service.web_client_id = self.app.client_id
        service.save()
        self.app.client_type = "public"
        self.app.save()
        with self.assertRaisesRegex(RuntimeError, "missing/public"):
            self.plan(apps, "default")
        self.app.client_type = "confidential"
        self.app.save()
        duplicate = GameClient.objects.create(
            id="duplicate", name="Duplicate", web_client_id="website", secret_hash="a" * 64
        )
        with self.assertRaisesRegex(RuntimeError, "multiple services"):
            self.plan(apps, "default")
        duplicate.delete()
        Application.objects.create(client_id="site", client_type="confidential", client_secret="another")
        with self.assertRaisesRegex(RuntimeError, "shadows"):
            self.plan(apps, "default")
        service.web_client_id = ""
        service.save()
        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            self.plan(apps, "default")
