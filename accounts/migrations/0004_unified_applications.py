"""Unify service identities with OAuth Applications. Requires a stopped SSO writer.

Intentionally irreversible: after new applications/roles exist, downgrading would
lose identity information. Restore the stopped-writer backup before reopening,
or roll forward after traffic resumes. Legacy tables remain for audit only.
"""

import secrets

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migration_plan(apps, alias):
    Application = apps.get_model("oauth2_provider", "Application")
    GameClient = apps.get_model("accounts", "GameClient")
    linked = set()
    plan = []
    for service in GameClient.objects.using(alias).order_by("pk"):
        app = None
        if service.web_client_id:
            app = Application.objects.using(alias).filter(client_id=service.web_client_id).first()
            if app is None or app.client_type != "confidential":
                raise RuntimeError(
                    "Migration blocked: service references a missing/public OAuth application."
                )
            if app.pk in linked:
                raise RuntimeError("Migration blocked: multiple services reference one OAuth application.")
            linked.add(app.pk)
        else:
            if Application.objects.using(alias).filter(client_id=service.pk).exists():
                raise RuntimeError(
                    "Migration blocked: standalone service ID conflicts with an Application ID."
                )
        shadow = Application.objects.using(alias).filter(client_id=service.pk).first()
        if shadow and (app is None or shadow.pk != app.pk):
            raise RuntimeError("Migration blocked: legacy service ID shadows another Application ID.")
        plan.append((service, app))
    return plan


def migrate_identities(apps, schema_editor):
    alias = schema_editor.connection.alias
    plan = migration_plan(apps, alias)  # Validate the whole plan before writing anything.
    Application = apps.get_model("oauth2_provider", "Application")
    Access = apps.get_model("accounts", "ApplicationAccess")
    Role = apps.get_model("accounts", "ClientRole")
    Session = apps.get_model("accounts", "GameSession")
    for service, app in plan:
        if app is None:
            app = Application.objects.using(alias).create(
                client_id=service.pk,
                name=service.name,
                client_secret=secrets.token_hex(32),
                hash_client_secret=True,
                client_type="confidential",
                authorization_grant_type="client-credentials",
                redirect_uris="",
                algorithm="RS256",
            )
        Access.objects.using(alias).create(
            application_id=app.pk,
            enabled=service.enabled,
            allow_game=True,
            allow_profiles=True,
            allow_web=bool(service.web_client_id),
            legacy_service_id=service.pk,
            legacy_secret_hash=service.secret_hash,
            accept_legacy=True,
        )
        Role.objects.using(alias).filter(client_id=service.pk).update(application_id=app.pk)
        Session.objects.using(alias).filter(client_id=service.pk).update(application_id=app.pk)
    for app in Application.objects.using(alias).all():
        Access.objects.using(alias).get_or_create(application_id=app.pk)
    # PostgreSQL defers FK triggers; flush them before the subsequent FK/column DDL.
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_preferred_language"),
        ("oauth2_provider", "0022_refreshtoken_token_family_index"),
    ]
    operations = [
        migrations.CreateModel(
            name="ApplicationAccess",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("enabled", models.BooleanField(default=True, verbose_name="启用应用")),
                ("allow_game", models.BooleanField(default=False, verbose_name="允许游戏登录和会话接口")),
                ("allow_profiles", models.BooleanField(default=False, verbose_name="允许用户资料查询")),
                ("allow_web", models.BooleanField(default=False, verbose_name="允许网站令牌查询和撤销")),
                (
                    "legacy_service_id",
                    models.SlugField(blank=True, editable=False, max_length=32, null=True, unique=True),
                ),
                ("legacy_secret_hash", models.CharField(blank=True, editable=False, max_length=64)),
                ("accept_legacy", models.BooleanField(default=False, verbose_name="接受迁移前的服务凭据")),
                (
                    "application",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="service_access",
                        to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "应用接口权限",
                "verbose_name_plural": "应用接口权限",
            },
        ),
        migrations.AddField(
            model_name="clientrole",
            name="application",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="gamesession",
            name="application",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
            ),
        ),
        migrations.RunPython(migrate_identities),
        migrations.RemoveConstraint(model_name="clientrole", name="sso_unique_role"),
        migrations.RemoveField(model_name="clientrole", name="client"),
        migrations.RenameField(model_name="clientrole", old_name="application", new_name="client"),
        migrations.AlterField(
            model_name="clientrole",
            name="client",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
                verbose_name="Application",
            ),
        ),
        migrations.RemoveField(model_name="gamesession", name="client"),
        migrations.RenameField(model_name="gamesession", old_name="application", new_name="client"),
        migrations.AlterField(
            model_name="gamesession",
            name="client",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
                verbose_name="Application",
            ),
        ),
        migrations.AddConstraint(
            model_name="clientrole",
            constraint=models.UniqueConstraint(fields=("user", "client"), name="sso_unique_role"),
        ),
    ]
