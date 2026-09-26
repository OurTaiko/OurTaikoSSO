from importlib import import_module

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Read-only preflight for the Application unification; never prints credentials."

    def handle(self, *args, **options):
        if MigrationRecorder.Migration.objects.filter(
            app="accounts", name="0004_unified_applications"
        ).exists():
            self.stdout.write(
                "Already migrated; legacy records are now historical and are not a new migration plan."
            )
            return
        # Read the historical schema; current runtime no longer contains GameClient.
        apps = (
            MigrationExecutor(connection)
            .loader.project_state(
                [
                    ("accounts", "0003_user_preferred_language"),
                    ("oauth2_provider", "0022_refreshtoken_token_family_index"),
                ]
            )
            .apps
        )
        migration = import_module("accounts.migrations.0004_unified_applications")
        try:
            plan = migration.migration_plan(apps, "default")
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"PASS: {len(plan)} legacy services; {sum(app is None for _, app in plan)} new Applications."
        )
