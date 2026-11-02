import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Bindings move from Sakura alone to one set per Application.

    0008 shipped with no binding rows ever written, so the table is recreated rather than altered.
    """

    dependencies = [
        ("accounts", "0008_external_links"),
        migrations.swappable_dependency(settings.OAUTH2_PROVIDER_APPLICATION_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="ExternalLink"),
        migrations.CreateModel(
            name="ExternalLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("external_id", models.CharField(max_length=64, verbose_name="外部账号 ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="绑定时间")),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_links",
                        to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
                        verbose_name="Application",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "外源账号绑定",
                "verbose_name_plural": "外源账号绑定",
                "constraints": [
                    models.UniqueConstraint(fields=("application", "user"), name="sso_unique_external_user"),
                    models.UniqueConstraint(
                        fields=("application", "external_id"), name="sso_unique_external_id"
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="applicationaccess",
            name="allow_external_link",
            field=models.BooleanField(default=False, verbose_name="允许外源账号查询和绑定"),
        ),
    ]
