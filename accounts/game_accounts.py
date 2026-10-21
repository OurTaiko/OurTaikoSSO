"""Game accounts: the game signs in once with a username and password and keeps a token
that slides forward on each use. The token is only ever sent here; the game trades it
for ordinary per-service game sessions, so business services keep their isolation."""

import json
import re
import secrets
import unicodedata
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from oauth2_provider.models import Application

from .api import TOKEN_TTL, problem, user_payload
from .models import ApplicationAccess, GameAccount, GameSession, User
from .security import digest, login_allowed

# Each use moves the expiry forward, but at most once a minute to spare the database.
TOUCH_INTERVAL = timedelta(minutes=1)


def idle_lifetime():
    return timedelta(days=settings.GAME_ACCOUNT_IDLE_DAYS)


def game_client():
    """The OurTaikoPlay Application, or None when it is missing or disabled."""
    client = Application.objects.filter(client_id=settings.GAME_ACCOUNT_CLIENT_ID).first()
    access = ApplicationAccess.objects.filter(application=client).first() if client else None
    return client if access and access.enabled else None


def account_user(user):
    return {
        "id": user.pk,
        "username": user.username,
        "nickname": user.nickname,
        "avatarUrl": user.absolute_avatar_url,
        "preferredLanguage": user.preferred_language,
    }


def no_store(response):
    response["Cache-Control"] = "no-store, private"
    return response


def game_endpoint(method):
    """A route for the game itself: no browser origin, JSON bodies, no cookies."""

    def decorate(view):
        @csrf_exempt
        @sensitive_post_parameters()
        @wraps(view)
        def wrapped(request):
            if request.method != method:
                response = problem(405, "METHOD_NOT_ALLOWED", "请使用 " + method)
                response["Allow"] = method
                return response
            if request.headers.get("Origin"):
                return problem(403, "ORIGIN_INVALID", "此接口仅供游戏调用")
            body = {}
            if method == "POST":
                if request.content_type != "application/json":
                    return problem(415, "CONTENT_TYPE_INVALID", "请使用 JSON")
                try:
                    body = json.loads(request.body)
                except (ValueError, UnicodeError):
                    return problem(400, "REQUEST_INVALID", "请求格式不正确")
                if not isinstance(body, dict):
                    return problem(400, "REQUEST_INVALID", "请求必须是 JSON 对象")
            return no_store(view(request, body))

        return wrapped

    return decorate


def bearer(request):
    match = re.fullmatch(r"Bearer ([0-9a-f]{64})", request.headers.get("Authorization", ""))
    return match.group(1) if match else None


def signed_in(request):
    """The live account behind the bearer token, its expiry moved forward; else None."""
    token, client = bearer(request), game_client()
    if token is None or client is None:
        return None
    now = timezone.now()
    account = (
        GameAccount.objects.select_related("user")
        .filter(token_hash=digest(token), client=client, expires_at__gt=now, user__is_active=True)
        .first()
    )
    if account is None or account.auth_version != account.user.auth_version:
        return None
    if now - account.last_used_at >= TOUCH_INTERVAL:
        account.last_used_at, account.expires_at = now, now + idle_lifetime()
        GameAccount.objects.filter(pk=account.pk).update(
            last_used_at=account.last_used_at, expires_at=account.expires_at
        )
    return account


def device_name(value):
    if not isinstance(value, str):
        return ""
    printable = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
    return printable.strip()[:80]


@game_endpoint("POST")
def login(request, body):
    if (
        not {"username", "password"} <= set(body) <= {"username", "password", "deviceName"}
        or not isinstance(body["username"], str)
        or not isinstance(body["password"], str)
    ):
        return problem(400, "REQUEST_INVALID", "需要 username 和 password")
    if len(body["username"]) > 24 or not 1 <= len(body["password"]) <= 1024:
        return problem(401, "LOGIN_INVALID", "用户名或密码不正确")
    client = game_client()
    if client is None:
        return problem(403, "GAME_DISABLED", "游戏登录暂不可用")
    if not login_allowed(request, body["username"]):
        response = problem(429, "RATE_LIMITED", "尝试次数过多，请稍后重试")
        response["Retry-After"] = "600"
        return response
    user = authenticate(request, username=body["username"], password=body["password"])
    if user is None:
        return problem(401, "LOGIN_INVALID", "用户名或密码不正确")
    with transaction.atomic():
        # A password change between authentication and issuance must not create a valid sign-in.
        locked = User.objects.select_for_update().get(pk=user.pk)
        if not locked.is_active or locked.password != user.password:
            return problem(401, "LOGIN_INVALID", "登录状态已改变，请重新登录")
        token, now = secrets.token_hex(32), timezone.now()
        account = GameAccount.objects.create(
            token_hash=digest(token),
            user=locked,
            client=client,
            auth_version=locked.auth_version,
            device_name=device_name(body.get("deviceName")),
            last_used_at=now,
            expires_at=now + idle_lifetime(),
        )
    return JsonResponse(
        {"token": token, "expiresAt": account.expires_at.isoformat(), "user": account_user(locked)}
    )


@game_endpoint("GET")
def session(request, body):
    account = signed_in(request)
    if account is None:
        return problem(401, "UNAUTHORIZED", "登录已过期，请重新登录")
    return JsonResponse({"expiresAt": account.expires_at.isoformat(), "user": account_user(account.user)})


@game_endpoint("POST")
def logout(request, body):
    token = bearer(request)
    if token:
        GameAccount.objects.filter(token_hash=digest(token)).delete()
    return JsonResponse({"ok": True})


@game_endpoint("POST")
def service_token(request, body):
    """Trade the account for a game session of one service, as its game/login would give."""
    account = signed_in(request)
    if account is None:
        return problem(401, "UNAUTHORIZED", "登录已过期，请重新登录")
    service = body.get("service")
    if set(body) != {"service"} or not isinstance(service, str) or not 1 <= len(service) <= 100:
        return problem(400, "REQUEST_INVALID", "需要 service")
    target = Application.objects.filter(client_id=service).first()
    access = ApplicationAccess.objects.filter(application=target).first() if target else None
    if not (
        access
        and access.enabled
        and access.allow_game
        and access.allow_account_tokens
        and target.client_type == Application.CLIENT_CONFIDENTIAL
    ):
        return problem(403, "SERVICE_FORBIDDEN", "该服务不接受游戏账号登录")
    token = secrets.token_hex(32)
    GameSession.objects.create(
        token_hash=digest(token),
        user=account.user,
        client=target,
        auth_version=account.auth_version,
        expires_at=timezone.now() + timedelta(seconds=TOKEN_TTL),
    )
    return JsonResponse(
        {"user": user_payload(account.user, target), "accessToken": token, "expiresIn": TOKEN_TTL}
    )
