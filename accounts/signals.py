from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.contrib.sessions.models import Session
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import BrowserSession, User
from .security import revoke_user_tokens


@receiver(pre_save, sender=User)
def detect_credentials_change(sender, instance, **kwargs):
    fields = kwargs.get("update_fields")
    if fields is not None and not {"password", "is_active"}.intersection(fields):
        instance._credentials_changed = False
        return
    old = sender.objects.filter(pk=instance.pk).values("password", "is_active", "auth_version").first()
    instance._credentials_changed = bool(
        old
        and (
            ((fields is None or "password" in fields) and old["password"] != instance.password)
            or ((fields is None or "is_active" in fields) and old["is_active"] and not instance.is_active)
        )
    )
    if instance._credentials_changed:
        instance.auth_version = old["auth_version"] + 1


@receiver(post_save, sender=User)
def invalidate_credentials(sender, instance, **kwargs):
    if getattr(instance, "_credentials_changed", False):
        # Also update when a password hasher upgrade uses save(update_fields=['password']).
        sender.objects.filter(pk=instance.pk).update(auth_version=instance.auth_version)
        revoke_user_tokens(instance)
        Session.objects.filter(
            session_key__in=BrowserSession.objects.filter(user=instance).values("session_key")
        ).delete()
        BrowserSession.objects.filter(user=instance).delete()


@receiver(user_logged_in)
def record_browser_session(sender, request, user, **kwargs):
    BrowserSession.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={"user": user, "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200]},
    )


@receiver(user_logged_out)
def remove_browser_session(sender, request, **kwargs):
    if request:
        BrowserSession.objects.filter(session_key=request.session.session_key).delete()


@receiver(post_delete, sender=User)
def delete_avatar(sender, instance, **kwargs):
    if instance.avatar:
        from .avatars import _delete_later

        _delete_later(instance.avatar)
