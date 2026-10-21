import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import RateBucket


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def allow_attempt(key, limit=10, seconds=600):
    """Database-backed, atomic fixed window; store neither passwords nor raw addresses."""
    now = timezone.now()
    with transaction.atomic():
        bucket, _ = RateBucket.objects.get_or_create(
            key=digest(key),
            defaults={"expires_at": now + timedelta(seconds=seconds)},
        )
        bucket = RateBucket.objects.select_for_update().get(pk=bucket.pk)
        if bucket.expires_at <= now:
            bucket.count, bucket.expires_at = 0, now + timedelta(seconds=seconds)
        bucket.count = min(bucket.count + 1, limit + 1)
        bucket.save(update_fields=["count", "expires_at"])
        return bucket.count <= limit


def login_allowed(request, username):
    # Forwarded headers are deliberately not trusted. Gateways must also limit their public login route.
    ip_ok = allow_attempt("login-ip:" + request.META.get("REMOTE_ADDR", "unknown"), 60)
    user_ok = allow_attempt("login-user:" + username[:100], 15)
    return ip_ok and user_ok


def revoke_user_tokens(user):
    from oauth2_provider.models import AccessToken, Grant, IDToken, RefreshToken

    from .models import GameAccount, GameSession

    GameAccount.objects.filter(user=user).delete()
    GameSession.objects.filter(user=user).delete()
    RefreshToken.objects.filter(user=user).delete()
    AccessToken.objects.filter(user=user).delete()
    IDToken.objects.filter(user=user).delete()
    Grant.objects.filter(user=user).delete()
