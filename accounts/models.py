import secrets
import unicodedata

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


def user_id():
    return secrets.token_hex(16)


def validate_nickname(value):
    if not value.strip() or len(value) > 40 or len(value.encode("utf-8")) > 160:
        raise ValidationError(_("昵称需为 1–40 个字符。"))
    if any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in value):
        raise ValidationError(_("昵称不能包含换行或控制字符。"))


class User(AbstractUser):
    id = models.CharField(primary_key=True, max_length=32, default=user_id, editable=False)
    username = models.CharField(
        max_length=24,
        unique=True,
        validators=[
            RegexValidator(r"^[A-Za-z0-9]{3,24}$", _("登录名需为 3–24 位英文字母或数字。")),
        ],
    )
    nickname = models.CharField(max_length=40, validators=[validate_nickname])
    email = models.EmailField(blank=True)
    email_verified = models.BooleanField(default=False)

    class Language(models.TextChoices):
        CHINESE = "zh-hans", "简体中文"
        ENGLISH = "en", "English"
        JAPANESE = "ja", "日本語"
        KOREAN = "ko", "한국어"

    preferred_language = models.CharField(
        max_length=16,
        choices=Language.choices,
        default=Language.CHINESE,
        verbose_name=_("偏好语言"),
    )
    auth_version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="sso_unique_email",
            )
        ]

    def __str__(self):
        return self.nickname or self.username


class GameClient(models.Model):
    """Frozen pre-unification records, retained only for migration audit/rollback."""

    id = models.SlugField(primary_key=True, max_length=32)
    name = models.CharField(max_length=80)
    secret_hash = models.CharField(max_length=64)
    enabled = models.BooleanField(default=True)
    web_client_id = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name


class ApplicationAccess(models.Model):
    """Capabilities and temporary legacy credentials of ONE OAuth Application."""

    application = models.OneToOneField(
        "oauth2_provider.Application", on_delete=models.CASCADE, related_name="service_access"
    )
    enabled = models.BooleanField(default=True, verbose_name="启用应用")
    allow_game = models.BooleanField(default=False, verbose_name="允许游戏登录和会话接口")
    allow_profiles = models.BooleanField(default=False, verbose_name="允许用户资料查询")
    allow_web = models.BooleanField(default=False, verbose_name="允许网站令牌查询和撤销")
    legacy_service_id = models.SlugField(max_length=32, null=True, blank=True, unique=True, editable=False)
    legacy_secret_hash = models.CharField(max_length=64, blank=True, editable=False)
    accept_legacy = models.BooleanField(default=False, verbose_name="接受迁移前的服务凭据")

    class Meta:
        verbose_name = "应用接口权限"
        verbose_name_plural = "应用接口权限"


class ClientRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    client = models.ForeignKey(
        "oauth2_provider.Application", on_delete=models.CASCADE, verbose_name="Application"
    )
    is_admin = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "client"], name="sso_unique_role")]


class GameSession(models.Model):
    token_hash = models.CharField(primary_key=True, max_length=64)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    client = models.ForeignKey(
        "oauth2_provider.Application", on_delete=models.CASCADE, verbose_name="Application"
    )
    auth_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)


class BrowserSession(models.Model):
    session_key = models.CharField(primary_key=True, max_length=40)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RateBucket(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
