from django.db import migrations, models


class Migration(migrations.Migration):
    """Bindings record how they were made; every existing row came from the partner's link call."""

    dependencies = [("accounts", "0009_external_links_per_application")]

    operations = [
        migrations.AddField(
            model_name="externallink",
            name="origin",
            field=models.CharField(
                choices=[
                    ("partner_link", "对方平台调用绑定接口"),
                    ("signup", "用对方平台账号首次登录时建号"),
                    ("user_link", "用户登录后主动关联"),
                ],
                default="partner_link",
                max_length=16,
                verbose_name="来源",
            ),
        ),
    ]
