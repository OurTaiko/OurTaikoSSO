import secrets
import unicodedata

from django.conf import settings
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
    # Name inside the avatar storage (S3 prefix "sso/"), e.g. "avatars/<id>/<sha>.webp".
    avatar = models.CharField(max_length=128, blank=True, editable=False)

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

    @property
    def avatar_url(self):
        """Content-addressed CDN URL, or the local avatar route when CDN is disabled."""
        if not self.avatar:
            return ""
        from django.urls import reverse

        digest = self.avatar.rsplit("/", 1)[-1].removesuffix(".webp")
        if settings.AVATAR_CDN_BASE_URL:
            return f"{settings.AVATAR_CDN_BASE_URL}/{self.pk}/{digest}.webp"
        return reverse("avatar", args=[self.pk, digest])

    @property
    def absolute_avatar_url(self):
        url = self.avatar_url
        if not url or settings.AVATAR_CDN_BASE_URL:
            return url
        return settings.SSO_BASE_URL + url


class ApplicationAccess(models.Model):
    """Capabilities of the canonical OAuth Application."""

    application = models.OneToOneField(
        "oauth2_provider.Application", on_delete=models.CASCADE, related_name="service_access"
    )
    enabled = models.BooleanField(default=True, verbose_name="启用应用")
    allow_game = models.BooleanField(default=False, verbose_name="允许游戏登录和会话接口")
    allow_profiles = models.BooleanField(default=False, verbose_name="允许用户资料查询")
    allow_web = models.BooleanField(default=False, verbose_name="允许网站令牌查询和撤销")
    allow_account_tokens = models.BooleanField(default=False, verbose_name="允许游戏账号换取本应用的游戏会话")
    allow_external_link = models.BooleanField(default=False, verbose_name="允许外源账号查询和绑定")

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


class GameAccount(models.Model):
    """A game client's sign-in: a long-lived token that slides forward on each use.

    It is only ever sent to the SSO, which trades it for per-service game sessions.
    """

    token_hash = models.CharField(primary_key=True, max_length=64)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    client = models.ForeignKey(
        "oauth2_provider.Application", on_delete=models.CASCADE, verbose_name="Application"
    )
    auth_version = models.PositiveIntegerField()
    device_name = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)


class BrowserSession(models.Model):
    session_key = models.CharField(primary_key=True, max_length=40)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ExternalLink(models.Model):
    """Binds an OurTaiko account to an account on another platform, one Application per platform.

    Within one Application the binding is one-to-one: each OurTaiko account and each external ID
    appears at most once. An account may be bound on several Applications.
    """

    application = models.ForeignKey(
        "oauth2_provider.Application",
        on_delete=models.CASCADE,
        related_name="external_links",
        verbose_name="Application",
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="external_links")
    external_id = models.CharField(max_length=64, verbose_name="外部账号 ID")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="绑定时间")

    class Meta:
        verbose_name = "外源账号绑定"
        verbose_name_plural = "外源账号绑定"
        constraints = [
            models.UniqueConstraint(fields=["application", "user"], name="sso_unique_external_user"),
            models.UniqueConstraint(fields=["application", "external_id"], name="sso_unique_external_id"),
        ]


class ExternalAudit(models.Model):
    """One row per authenticated call to the external interface, kept EXTERNAL_AUDIT_DAYS days.

    It never stores email, nickname or addresses; target_id is not a foreign key so the record
    survives the account.
    """

    application = models.ForeignKey(
        "oauth2_provider.Application", null=True, on_delete=models.SET_NULL, verbose_name="Application"
    )
    action = models.CharField(max_length=16)
    target_id = models.CharField(max_length=32, blank=True)
    external_id = models.CharField(max_length=64, blank=True)
    result = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "外源接口审计"
        verbose_name_plural = "外源接口审计"


class RateBucket(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
