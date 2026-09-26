from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from oauth2_provider.models import Application


class ApplicationMigrationPreflightTests(TransactionTestCase):
    def setUp(self):
        self.historical_apps = (
            MigrationExecutor(connection)
            .loader.project_state(
                [
                    ("accounts", "0003_user_preferred_language"),
                    ("oauth2_provider", "0022_refreshtoken_token_family_index"),
                ]
            )
            .apps
        )
        self.GameClient = self.historical_apps.get_model("accounts", "GameClient")
        with connection.schema_editor() as schema:
            schema.create_model(self.GameClient)
        self.plan = import_module("accounts.migrations.0004_unified_applications").migration_plan
        self.app = Application.objects.create(
            client_id="website",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            client_secret="test-secret",
        )

    def tearDown(self):
        with connection.schema_editor() as schema:
            schema.delete_model(self.GameClient)
        super().tearDown()

    def test_explicit_binding_and_service_only_plan_are_read_only(self):
        self.GameClient.objects.create(id="site", name="Site", web_client_id="website", secret_hash="a" * 64)
        self.GameClient.objects.create(id="standalone", name="Server", secret_hash="b" * 64)
        before = list(Application.objects.values())
        result = self.plan(self.historical_apps, "default")
        self.assertEqual([app.pk if app else None for _, app in result], [self.app.pk, None])
        self.assertEqual(before, list(Application.objects.values()))

    def test_missing_public_duplicate_and_shadowed_bindings_fail_closed(self):
        service = self.GameClient.objects.create(
            id="site", name="Site", web_client_id="missing", secret_hash="a" * 64
        )
        with self.assertRaisesRegex(RuntimeError, "missing/public"):
            self.plan(self.historical_apps, "default")
        service.web_client_id = self.app.client_id
        service.save()
        self.app.client_type = "public"
        self.app.save()
        with self.assertRaisesRegex(RuntimeError, "missing/public"):
            self.plan(self.historical_apps, "default")
        self.app.client_type = "confidential"
        self.app.save()
        duplicate = self.GameClient.objects.create(
            id="duplicate", name="Duplicate", web_client_id="website", secret_hash="a" * 64
        )
        with self.assertRaisesRegex(RuntimeError, "multiple services"):
            self.plan(self.historical_apps, "default")
        # The historical model has old relations; delete only its synthetic table row.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM accounts_gameclient WHERE id = %s", [duplicate.pk])
        Application.objects.create(client_id="site", client_type="confidential", client_secret="another")
        with self.assertRaisesRegex(RuntimeError, "shadows"):
            self.plan(self.historical_apps, "default")
        service.web_client_id = ""
        service.save()
        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            self.plan(self.historical_apps, "default")
