import secrets

from django import forms
from django.contrib import admin
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

from .models import ApplicationAccess, ClientRole, User


@admin.register(User)
class AccountAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("OurTaiko", {"fields": ("id", "nickname", "preferred_language", "email_verified", "auth_version")}),
    )
    readonly_fields = ("id", "auth_version")
    add_fieldsets = UserAdmin.add_fieldsets + (("OurTaiko", {"fields": ("nickname", "email")}),)
    list_display = ("username", "nickname", "email_verified", "is_active")


ACCESS_FIELDS = ("enabled", "allow_game", "allow_profiles", "allow_web", "accept_legacy")


class UnifiedApplicationForm(forms.ModelForm):
    enabled = forms.BooleanField(label="启用应用", required=False, initial=True)
    allow_game = forms.BooleanField(label="允许游戏登录和会话接口", required=False)
    allow_profiles = forms.BooleanField(label="允许用户资料查询", required=False)
    allow_web = forms.BooleanField(label="允许网站令牌查询和撤销", required=False)
    accept_legacy = forms.BooleanField(
        label="接受迁移前的服务凭据",
        required=False,
        help_text="仅用于已有服务的过渡。关闭前须先更新业务后端的凭据并验证。",
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
            data.get(field) for field in ("allow_game", "allow_profiles", "allow_web", "accept_legacy")
        ):
            raise forms.ValidationError("内部接口只允许能够保管密钥的 confidential 后端应用。")
        if (
            data.get("accept_legacy")
            and not ApplicationAccess.objects.filter(
                application_id=self.instance.pk, legacy_service_id__isnull=False
            ).exists()
        ):
            self.add_error("accept_legacy", "此应用没有迁移前的服务凭据。")
        client_id = data.get("client_id")
        if (
            client_id
            and ApplicationAccess.objects.filter(legacy_service_id=client_id)
            .exclude(application_id=self.instance.pk)
            .exists()
        ):
            self.add_error("client_id", "此 ID 已被迁移前的服务使用。")
        return data


class UnifiedApplicationAdmin(admin.ModelAdmin):
    form = UnifiedApplicationForm
    list_display = ("name", "client_id", "client_type", "authorization_grant_type")
    search_fields = ("name", "client_id")
    fieldsets = (
        ("应用", {"fields": ("name", "client_id", "enabled", "credentials")}),
        ("登录方式", {"fields": ("client_type", "authorization_grant_type", "redirect_uris", "algorithm")}),
        ("后端接口权限", {"fields": ("allow_game", "allow_profiles", "allow_web")}),
        ("迁移兼容", {"fields": ("legacy_id", "accept_legacy")}),
    )

    def get_readonly_fields(self, request, obj=None):
        return ("credentials", "legacy_id", "client_id") if obj else ("credentials", "legacy_id")

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

    @admin.display(description="旧服务 ID")
    def legacy_id(self, obj):
        if not obj or not obj.pk:
            return "无（新应用不需要 Game client）"
        access = ApplicationAccess.objects.filter(application=obj).first()
        return access.legacy_service_id if access and access.legacy_service_id else "无"

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
                # Rotating the canonical secret also revokes its compatibility credential.
                ApplicationAccess.objects.filter(application=obj).update(accept_legacy=False)
                self.log_change(request, obj, "Reset application secret and disabled legacy credential")
        return self.secret_response(request, obj, secret)


@admin.register(ClientRole)
class ClientRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "client", "is_admin")
    list_select_related = ("user", "client")
    list_filter = ("client", "is_admin")
    search_fields = ("user__username", "client__name")


admin.site.site_header = _("OurTaiko 账号管理")
