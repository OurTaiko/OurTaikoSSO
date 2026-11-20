import hashlib
from datetime import timedelta
from ipaddress import ip_address

from django.conf import settings
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


def client_ip(request):
    """Use a single gateway-supplied address only when the connection came from a configured proxy."""
    remote = request.META.get("REMOTE_ADDR", "")
    try:
        peer = ip_address(remote)
    except ValueError:
        return "unknown"
    if any(peer in network for network in settings.TRUSTED_PROXY_CIDRS):
        forwarded = request.META.get("HTTP_X_REAL_IP", "").strip()
        if forwarded and "%" not in forwarded:
            try:
                return str(ip_address(forwarded))
            except ValueError:
                pass
    return str(peer)


def login_allowed(request, username):
    ip_ok = allow_attempt("login-ip:" + client_ip(request), 60)
    # One count for a login name however it is capitalised, as it is one account.
    user_ok = allow_attempt("login-user:" + username[:100].lower(), 15)
    return ip_ok and user_ok


def revoke_user_tokens(user):
    from oauth2_provider.models import AccessToken, Grant, IDToken, RefreshToken

    from .models import GameAccount, GameAuthorization, GameSession

    GameAccount.objects.filter(user=user).delete()
    GameAuthorization.objects.filter(user=user).delete()
    GameSession.objects.filter(user=user).delete()
    RefreshToken.objects.filter(user=user).delete()
    AccessToken.objects.filter(user=user).delete()
    IDToken.objects.filter(user=user).delete()
    Grant.objects.filter(user=user).delete()
