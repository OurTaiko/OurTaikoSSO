import secrets

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

from .models import ClientRole, GameClient, User
from .security import digest


@admin.register(User)
class AccountAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("OurTaiko", {"fields": ("id", "nickname", "preferred_language", "email_verified", "auth_version")}),
    )
    readonly_fields = ("id", "auth_version")
    add_fieldsets = UserAdmin.add_fieldsets + (("OurTaiko", {"fields": ("nickname", "email")}),)
    list_display = ("username", "nickname", "email_verified", "is_active")


@admin.register(GameClient)
class ClientAdmin(admin.ModelAdmin):
    fields = ("id", "name", "enabled", "web_client_id", "service_credentials")
    list_display = ("id", "name", "enabled", "web_client_id")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if object_id is None:
            extra_context = {**(extra_context or {}), "show_save_and_continue": False,
                             "show_save_and_add_another": False}
        return super().changeform_view(request, object_id, form_url, extra_context)

    def get_readonly_fields(self, request, obj=None):
        return ("id", "service_credentials") if obj else ("service_credentials",)

    @admin.display(description="服务密钥")
    def service_credentials(self, obj):
        if not obj or not obj.pk:
            return "保存后自动生成密钥，仅显示一次。ESE 的 web_client_id 留空即可。"
        return format_html(
            '<a href="{}">重置服务密钥</a>（无法查看原密钥）',
            reverse("admin:accounts_gameclient_reset_secret", args=[obj.pk]),
        )

    def get_urls(self):
        return [
            path(
                "<slug:object_id>/reset-secret/",
                self.admin_site.admin_view(self.reset_secret),
                name="accounts_gameclient_reset_secret",
            ),
        ] + super().get_urls()

    def save_model(self, request, obj, form, change):
        if not change:
            request.generated_service_secret = secrets.token_hex(32)
            obj.secret_hash = digest(request.generated_service_secret)
        super().save_model(request, obj, form, change)

    def secret_response(self, request, obj, secret=None):
        response = TemplateResponse(request, "admin/accounts/gameclient/secret.html", {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": obj,
            "title": "服务密钥已生成" if secret else "重置服务密钥",
            "service_secret": secret,
            "change_url": reverse("admin:accounts_gameclient_change", args=[obj.pk]),
        })
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def response_add(self, request, obj, post_url_continue=None):
        return self.secret_response(request, obj, request.generated_service_secret)

    @method_decorator(require_http_methods(["GET", "POST"]))
    def reset_secret(self, request, object_id):
        with transaction.atomic():
            obj = self.model.objects.select_for_update().filter(pk=object_id).first()
            if obj is None:
                raise Http404
            if not self.has_change_permission(request, obj):
                raise PermissionDenied
            secret = None
            if request.method == "POST":
                secret = secrets.token_hex(32)
                obj.secret_hash = digest(secret)
                obj.save(update_fields=["secret_hash"])
                self.log_change(request, obj, "Reset service secret")
        return self.secret_response(request, obj, secret)


admin.site.register(ClientRole)
admin.site.site_header = _("OurTaiko 账号管理")
