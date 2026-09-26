from importlib import import_module

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
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
        migration = import_module("accounts.migrations.0004_unified_applications")
        try:
            plan = migration.migration_plan(apps, "default")
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"PASS: {len(plan)} legacy services; {sum(app is None for _, app in plan)} new Applications."
        )
