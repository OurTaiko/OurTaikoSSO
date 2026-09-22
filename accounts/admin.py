from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import ClientRole, GameClient, User


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
    fields = ("id", "name", "enabled", "web_client_id")
    readonly_fields = ("id",)

    def has_add_permission(self, request):
        return False


admin.site.register(ClientRole)
admin.site.site_header = _("OurTaiko 账号管理")
