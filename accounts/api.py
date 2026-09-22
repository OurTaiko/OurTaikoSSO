import json
import re
import secrets
from datetime import timedelta
from functools import wraps

from django.contrib.auth import authenticate
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from oauth2_provider.models import AccessToken

from .models import ClientRole, GameClient, GameSession, User
from .security import digest, login_allowed

TOKEN_TTL = 7 * 86400


def problem(status, code, message):
    return JsonResponse({"code": code, "message": message}, status=status)


def service_endpoint(view):
    @csrf_exempt
    @sensitive_post_parameters()
    @wraps(view)
    def wrapped(request):
        if request.method != "POST":
            response = problem(405, "METHOD_NOT_ALLOWED", "请使用 POST")
            response["Allow"] = "POST"
            return response
        if request.headers.get("Origin"):
            return problem(403, "ORIGIN_INVALID", "此接口仅供业务后端调用")
        client = GameClient.objects.filter(pk=request.headers.get("X-Service-ID", ""), enabled=True).first()
        key = request.headers.get("X-Service-Key", "")
        if (
            not client
            or not re.fullmatch(r"[0-9a-f]{64}", key)
            or not secrets.compare_digest(client.secret_hash, digest(key))
        ):
            return problem(401, "SERVICE_UNAUTHORIZED", "业务服务凭证无效")
        if request.content_type != "application/json":
            return problem(415, "CONTENT_TYPE_INVALID", "请使用 JSON")
        try:
            body = json.loads(request.body)
        except (ValueError, UnicodeError):
            return problem(400, "REQUEST_INVALID", "请求格式不正确")
        if not isinstance(body, dict):
            return problem(400, "REQUEST_INVALID", "请求必须是 JSON 对象")
        return view(request, client, body)

    return wrapped


def user_payload(user, client):
    return {
        "id": user.pk,
        "username": user.username,
        "nickname": user.nickname,
        "preferredLanguage": user.preferred_language,
        "emailVerified": user.email_verified,
        "isAdmin": ClientRole.objects.filter(user=user, client=client, is_admin=True).exists(),
    }


@service_endpoint
def game_login(request, client, body):
    if set(body) != {"username", "password"} or not all(isinstance(v, str) for v in body.values()):
        return problem(400, "REQUEST_INVALID", "需要 username 和 password")
    if len(body["username"]) > 24 or not 1 <= len(body["password"]) <= 1024:
        return problem(401, "LOGIN_INVALID", "用户名或密码不正确")
    if not login_allowed(request, body["username"]):
        response = problem(429, "RATE_LIMITED", "尝试次数过多，请稍后重试")
        response["Retry-After"] = "600"
        return response
    user = authenticate(request, username=body["username"], password=body["password"])
    if user is None:
        return problem(401, "LOGIN_INVALID", "用户名或密码不正确")
    with transaction.atomic():
        # A password change between authentication and issuance must not create a valid new session.
        locked = User.objects.select_for_update().get(pk=user.pk)
        if not locked.is_active or locked.password != user.password:
            return problem(401, "LOGIN_INVALID", "登录状态已改变，请重新登录")
        token = secrets.token_hex(32)
        GameSession.objects.create(
            token_hash=digest(token),
            user=locked,
            client=client,
            auth_version=locked.auth_version,
            expires_at=timezone.now() + timedelta(seconds=TOKEN_TTL),
        )
    return JsonResponse({"user": user_payload(locked, client), "accessToken": token, "expiresIn": TOKEN_TTL})


def find_session(client, body):
    token = body.get("accessToken")
    if set(body) != {"accessToken"} or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        return None
    session = (
        GameSession.objects.select_related("user")
        .filter(
            token_hash=digest(token),
            client=client,
            expires_at__gt=timezone.now(),
            user__is_active=True,
        )
        .first()
    )
    if session and session.auth_version == session.user.auth_version:
        return session
    return None


@service_endpoint
def introspect(request, client, body):
    session = find_session(client, body)
    if session is None:
        return problem(401, "UNAUTHORIZED", "会话无效或已过期")
    return JsonResponse(
        {"user": user_payload(session.user, client), "expiresAt": session.expires_at.isoformat()}
    )


@service_endpoint
def revoke(request, client, body):
    session = find_session(client, body)
    if session:
        session.delete()
    return JsonResponse({"ok": True})


@service_endpoint
def profiles(request, client, body):
    ids = body.get("userIds")
    if (
        set(body) != {"userIds"}
        or not isinstance(ids, list)
        or len(ids) > 100
        or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{32}", v) for v in ids)
    ):
        return problem(400, "REQUEST_INVALID", "userIds 必须包含至多 100 个用户 ID")
    users = User.objects.filter(pk__in=ids, is_active=True).values("id", "nickname")
    return JsonResponse({"users": list(users)})


def web_session(client, body):
    token = body.get("accessToken")
    if set(body) != {"accessToken"} or not isinstance(token, str) or not 1 <= len(token) <= 4096:
        return None
    if not client.web_client_id:
        return None
    from hashlib import sha256

    return (
        AccessToken.objects.select_related("user")
        .filter(
            token_checksum=sha256(token.encode()).hexdigest(),
            application__client_id=client.web_client_id,
            expires__gt=timezone.now(),
            user__is_active=True,
        )
        .first()
    )


@service_endpoint
def web_introspect(request, client, body):
    token = web_session(client, body)
    if token is None or "openid" not in token.scope.split():
        return problem(401, "UNAUTHORIZED", "会话无效或已过期")
    return JsonResponse({"user": user_payload(token.user, client), "expiresAt": token.expires.isoformat()})


@service_endpoint
def web_revoke(request, client, body):
    token = web_session(client, body)
    if token:
        token.revoke()
    return JsonResponse({"ok": True})


@service_endpoint
def search_profiles(request, client, body):
    query, after = body.get("query"), body.get("after", "")
    if set(body) - {"query", "after"} or not isinstance(query, str) or not 1 <= len(query) <= 200:
        return problem(400, "REQUEST_INVALID", "query 需为 1–200 个字符")
    if not isinstance(after, str) or (after and not re.fullmatch(r"[0-9a-f]{32}", after)):
        return problem(400, "REQUEST_INVALID", "after 无效")
    ids = list(
        User.objects.filter(is_active=True, nickname__icontains=query, id__gt=after)
        .order_by("id")
        .values_list("id", flat=True)[:1001]
    )
    return JsonResponse({"userIds": ids[:1000], "next": ids[999] if len(ids) > 1000 else ""})
