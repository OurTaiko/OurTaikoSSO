import secrets

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from oauth2_provider.models import Application

from .models import ApplicationAccess, ClientRole, ExternalAudit, ExternalLink, User


@admin.register(User)
class AccountAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("OurTaiko", {"fields": ("id", "nickname", "preferred_language", "email_verified", "auth_version")}),
    )
    readonly_fields = ("id", "auth_version")
    add_fieldsets = UserAdmin.add_fieldsets + (("OurTaiko", {"fields": ("nickname", "email")}),)
    list_display = ("username", "nickname", "email_verified", "is_active")


ACCESS_FIELDS = (
    "enabled",
    "allow_game",
    "allow_account_tokens",
    "allow_profiles",
    "allow_web",
    "allow_external_link",
)


class UnifiedApplicationForm(forms.ModelForm):
    enabled = forms.BooleanField(label="启用应用", required=False, initial=True)
    allow_game = forms.BooleanField(label="允许游戏登录和会话接口", required=False)
    allow_profiles = forms.BooleanField(label="允许用户资料查询", required=False)
    allow_web = forms.BooleanField(label="允许网站令牌查询和撤销", required=False)
    allow_account_tokens = forms.BooleanField(
        label="允许游戏账号换取本应用的游戏会话",
        required=False,
        help_text="开启后，OurTaikoPlay 登录的账号可以直接获得本应用的游戏会话，无需再输入密码。需同时允许游戏接口。",
    )
    allow_external_link = forms.BooleanField(
        label="允许外源账号查询和绑定",
        required=False,
        help_text="供另一个平台把它的账号与 OurTaiko 账号绑定：可以读取刚授权或已与本应用绑定的账号的昵称和邮箱，并建立绑定。只能开在 confidential 的网站登录应用上。",
    )

    class Meta:
        model = Application
        fields = (
            "name",
            "client_id",
            "client_type",
            "authorization_grant_type",
            "redirect_uris",
            "algorithm",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["authorization_grant_type"].choices = [
            ("authorization-code", "网站登录（Authorization code）"),
            ("client-credentials", "仅后端接口（不提供网站登录）"),
        ]
        self.fields["algorithm"].choices = [("RS256", "RS256")]
        self.fields["algorithm"].initial = "RS256"
        if self.instance.pk:
            access = ApplicationAccess.objects.filter(application=self.instance).first()
            if access:
                for field in ACCESS_FIELDS:
                    self.fields[field].initial = getattr(access, field)

    def clean(self):
        data = super().clean()
        if data.get("client_type") != Application.CLIENT_CONFIDENTIAL and any(
            data.get(field) for field in ("allow_game", "allow_account_tokens", "allow_profiles", "allow_web")
        ):
            raise forms.ValidationError("内部接口只允许能够保管密钥的 confidential 后端应用。")
        if data.get("allow_account_tokens") and not data.get("allow_game"):
            raise forms.ValidationError("游戏账号换取会话需要同时允许游戏登录和会话接口。")
        # Lookup and link are granted by the user signing in to this application through OIDC.
        if data.get("allow_external_link") and (
            data.get("client_type") != Application.CLIENT_CONFIDENTIAL
            or data.get("authorization_grant_type") != Application.GRANT_AUTHORIZATION_CODE
        ):
            raise forms.ValidationError("外源账号查询和绑定只能开在 confidential 的网站登录应用上。")
        return data


class UnifiedApplicationAdmin(admin.ModelAdmin):
    form = UnifiedApplicationForm
    list_display = ("name", "client_id", "client_type", "authorization_grant_type")
    search_fields = ("name", "client_id")
    fieldsets = (
        ("应用", {"fields": ("name", "client_id", "enabled", "credentials")}),
        ("登录方式", {"fields": ("client_type", "authorization_grant_type", "redirect_uris", "algorithm")}),
        (
            "后端接口权限",
            {"fields": ("allow_game", "allow_account_tokens", "allow_profiles", "allow_web", "allow_external_link")},
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        return ("credentials", "client_id") if obj else ("credentials",)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if object_id is None:
            extra_context = {
                **(extra_context or {}),
                "show_save_and_continue": False,
                "show_save_and_add_another": False,
            }
        return super().changeform_view(request, object_id, form_url, extra_context)

    @admin.display(description="应用密钥")
    def credentials(self, obj):
        if not obj or not obj.pk:
            return "保存后生成一份应用密钥，供网站登录及获准的后端接口共同使用，仅显示一次。"
        return format_html(
            '<a href="{}">重置应用密钥</a>（无法查看原文）',
            reverse("admin:oauth2_provider_application_reset_secret", args=[obj.pk]),
        )

    def get_urls(self):
        return [
            path(
                "<int:object_id>/reset-secret/",
                self.admin_site.admin_view(self.reset_secret),
                name="oauth2_provider_application_reset_secret",
            )
        ] + super().get_urls()

    def save_model(self, request, obj, form, change):
        if not change:
            request.generated_application_secret = secrets.token_hex(32)
            obj.client_secret = request.generated_application_secret
            obj.hash_client_secret = True
        super().save_model(request, obj, form, change)
        ApplicationAccess.objects.update_or_create(
            application=obj, defaults={field: bool(form.cleaned_data.get(field)) for field in ACCESS_FIELDS}
        )

    def secret_response(self, request, obj, secret=None):
        response = TemplateResponse(
            request,
            "admin/accounts/application_secret.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "original": obj,
                "title": "应用密钥已生成" if secret else "重置应用密钥",
                "application_secret": secret,
                "change_url": reverse("admin:oauth2_provider_application_change", args=[obj.pk]),
            },
        )
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def response_add(self, request, obj, post_url_continue=None):
        return self.secret_response(request, obj, request.generated_application_secret)

    @method_decorator(require_http_methods(["GET", "POST"]))
    def reset_secret(self, request, object_id):
        with transaction.atomic():
            obj = Application.objects.select_for_update().filter(pk=object_id).first()
            if obj is None:
                raise Http404
            if not self.has_change_permission(request, obj):
                raise PermissionDenied
            secret = None
            if request.method == "POST":
                secret = secrets.token_hex(32)
                obj.client_secret, obj.hash_client_secret = secret, True
                obj.save(update_fields=["client_secret", "hash_client_secret"])
                self.log_change(request, obj, "Reset application secret")
        return self.secret_response(request, obj, secret)


@admin.register(ClientRole)
class ClientRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "client", "is_admin")
    list_select_related = ("user", "client")
    list_filter = ("client", "is_admin")
    search_fields = ("user__username", "client__name")


@admin.register(ExternalLink)
class ExternalLinkAdmin(admin.ModelAdmin):
    """Superuser-only exact-pair unlink, with remote-first coordination for Sakura.

    Other Applications remain manual. The Viewer external client must be enabled
    and configured before automated two-sided deletion is allowed.
    """

    list_display = ("application", "user", "external_id", "created_at")
    list_select_related = ("application", "user")
    list_filter = ("application",)
    search_fields = ("user__username", "user__id", "external_id")
    readonly_fields = ("application", "user", "external_id", "created_at")
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def delete_model(self, request, obj):
        # Only the formal Sakura Application may use the Viewer reverse service.
        # The existing staging Application and unrelated clients never call it.
        if getattr(settings, "SAKURA_REVERSE_ENABLED", False) and obj.application.client_id == "Sakura":
            from .sakura import SakuraServiceError, unlink_viewer

            try:
                # Viewer ID is this application's external_id; the local user PK
                # is the OurTaiko 32-hex subject. Never swap the two arguments.
                unlink_viewer(str(obj.external_id), str(obj.user_id))
            except SakuraServiceError as error:
                if error.status != 404 or error.code != "NOT_FOUND":
                    # Stop before local deletion. Do not claim a two-sided success
                    # on timeout, 409 or an unreadable/unknown remote response.
                    raise PermissionDenied("Viewer 端解绑尚未确认，已保留双方当前记录") from None
            super().delete_model(request, obj)
            messages.success(request, "已按精确账号对协调 Viewer 端解绑，并删除 OurTaiko 本地绑定。")
            return

        super().delete_model(request, obj)
        messages.warning(
            request, f"已删除 OurTaiko 一侧的绑定。请通知 {obj.application.name} 删除外部账号 {obj.external_id} 的绑定。"
        )


@admin.register(ExternalAudit)
class ExternalAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "application", "action", "target_id", "external_id", "result")
    list_filter = ("action", "result")
    search_fields = ("target_id", "external_id")
    readonly_fields = list_display
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.site_header = _("OurTaiko 账号管理")
