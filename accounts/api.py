import json
import re
import secrets
from datetime import timedelta
from functools import wraps

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from oauth2_provider.models import AccessToken, Application, RefreshToken, revoke_access_token

from .models import ApplicationAccess, ClientRole, ExternalAudit, ExternalLink, GameSession, User
from .security import allow_attempt, digest, login_allowed

TOKEN_TTL = 7 * 86400


def problem(status, code, message):
    return JsonResponse({"code": code, "message": message}, status=status)


def envelope(status, code, message="", data=None):
    """The external interface's body: always {code, message, data}, never cached."""
    response = JsonResponse({"code": code, "message": message, "data": data}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def service_endpoint(capability, *, external=False):
    """Authenticate a business backend by its Application and require one ApplicationAccess flag.

    External endpoints answer every outcome, including authentication failures, in the
    {code, message, data} envelope and accept at most 32 KiB of JSON.
    """
    fail = envelope if external else problem

    def decorator(view):
        @csrf_exempt
        @sensitive_post_parameters()
        @wraps(view)
        def wrapped(request):
            if request.method != "POST":
                response = fail(405, "METHOD_NOT_ALLOWED", "请使用 POST")
                response["Allow"] = "POST"
                return response
            if request.headers.get("Origin"):
                return fail(403, "ORIGIN_INVALID", "此接口仅供业务后端调用")
            service_id = request.headers.get("X-Service-ID", "")
            key = request.headers.get("X-Service-Key", "")
            if not service_id or not key or len(service_id) > 100 or len(key) > 1024:
                return fail(401, "SERVICE_UNAUTHORIZED", "业务服务凭证无效")
            # The OAuth Application is the only service identity.
            client = Application.objects.filter(client_id=service_id).first()
            access = ApplicationAccess.objects.filter(application=client).first() if client else None
            authenticated = bool(
                access
                and access.enabled
                and client.client_type == Application.CLIENT_CONFIDENTIAL
                and client.hash_client_secret
                and check_password(key, client.client_secret)
            )
            if not authenticated:
                return fail(401, "SERVICE_UNAUTHORIZED", "业务服务凭证无效")
            if not getattr(access, capability):
                return fail(403, "SERVICE_FORBIDDEN", "此应用未获准使用该接口")
            if request.content_type != "application/json":
                return fail(415, "CONTENT_TYPE_INVALID", "请使用 JSON")
            try:
                raw = request.body
            except RequestDataTooBig:
                # DATA_UPLOAD_MAX_MEMORY_SIZE (32 KiB) is the protocol's body limit.
                return fail(400, "REQUEST_INVALID", "请求体过大")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                return fail(400, "REQUEST_INVALID", "请求格式不正确")
            if not isinstance(body, dict):
                return fail(400, "REQUEST_INVALID", "请求必须是 JSON 对象")
            return view(request, client, body)

        return wrapped

    return decorator


def avatar_url(user):
    return user.absolute_avatar_url


def user_payload(user, client):
    return {
        "id": user.pk,
        "username": user.username,
        "nickname": user.nickname,
        "avatarUrl": avatar_url(user),
        "preferredLanguage": user.preferred_language,
        "emailVerified": user.email_verified,
        "isAdmin": ClientRole.objects.filter(user=user, client=client, is_admin=True).exists(),
    }


@service_endpoint("allow_game")
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


@service_endpoint("allow_game")
def introspect(request, client, body):
    session = find_session(client, body)
    if session is None:
        return problem(401, "UNAUTHORIZED", "会话无效或已过期")
    return JsonResponse(
        {"user": user_payload(session.user, client), "expiresAt": session.expires_at.isoformat()}
    )


@service_endpoint("allow_game")
def revoke(request, client, body):
    session = find_session(client, body)
    if session:
        session.delete()
    return JsonResponse({"ok": True})


@service_endpoint("allow_profiles")
def profiles(request, client, body):
    ids = body.get("userIds")
    if (
        set(body) != {"userIds"}
        or not isinstance(ids, list)
        or len(ids) > 100
        or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{32}", v) for v in ids)
    ):
        return problem(400, "REQUEST_INVALID", "userIds 必须包含至多 100 个用户 ID")
    users = User.objects.filter(pk__in=ids, is_active=True).only("id", "nickname", "avatar")
    return JsonResponse(
        {"users": [{"id": u.pk, "nickname": u.nickname, "avatarUrl": avatar_url(u)} for u in users]}
    )


@service_endpoint("allow_profiles")
def profiles_by_username(request, client, body):
    usernames = body.get("usernames")
    if (
        set(body) != {"usernames"}
        or not isinstance(usernames, list)
        or len(usernames) > 100
        or not all(isinstance(v, str) and re.fullmatch(r"\S{1,24}", v) for v in usernames)
    ):
        return problem(400, "REQUEST_INVALID", "usernames 必须包含至多 100 个用户名，每个 1–24 字符且无空白")
    # Exact, case-sensitive matching agrees with login and username uniqueness.
    users = User.objects.filter(username__in=usernames, is_active=True).only("id", "username", "nickname", "avatar")
    return JsonResponse({"users": [
        {"id": u.pk, "username": u.username, "nickname": u.nickname, "avatarUrl": avatar_url(u)}
        for u in users
    ]})


def web_session(client, body, *, include_expired=False):
    token = body.get("accessToken")
    if set(body) != {"accessToken"} or not isinstance(token, str) or not 1 <= len(token) <= 4096:
        return None
    from hashlib import sha256

    return (
        AccessToken.objects.select_related("user")
        .filter(
            token_checksum=sha256(token.encode()).hexdigest(),
            application=client,
            user__is_active=True,
            **({} if include_expired else {"expires__gt": timezone.now()}),
        )
        .first()
    )


@service_endpoint("allow_web")
def web_introspect(request, client, body):
    token = web_session(client, body)
    if token is None or "openid" not in token.scope.split():
        return problem(401, "UNAUTHORIZED", "会话无效或已过期")
    return JsonResponse({"user": user_payload(token.user, client), "expiresAt": token.expires.isoformat()})


@service_endpoint("allow_web")
def web_revoke(request, client, body):
    if set(body) - {"accessToken", "refreshToken"}:
        return problem(400, "REQUEST_INVALID", "凭证格式不正确")
    refresh = body.get("refreshToken", "")
    if not isinstance(refresh, str) or len(refresh) > 4096:
        return problem(400, "REQUEST_INVALID", "凭证格式不正确")
    # A website may log out after its short-lived access token expired.
    token = web_session(client, {"accessToken": body.get("accessToken")}, include_expired=True)
    with transaction.atomic():
        renewal = (
            RefreshToken.objects.filter(application=client, token_checksum=digest(refresh)).first()
            if refresh
            else None
        )
        user_id = renewal.user_id if renewal else token.user_id if token else None
        if user_id:
            User.objects.select_for_update().get(pk=user_id)
        if renewal:
            if renewal.token_family:
                RefreshToken.revoke_family(renewal.token_family)
            else:
                renewal.revoke()
        if token and AccessToken.objects.filter(pk=token.pk).exists():
            revoke_access_token(token)
    return JsonResponse({"ok": True})


@service_endpoint("allow_profiles")
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


OURTAIKO_ID = re.compile(r"[0-9a-f]{32}")
EXTERNAL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def external_result(client, action, status, code, message="", data=None, *, target="", external=""):
    """Answer an external call and record it; the audit never holds email or nickname."""
    ExternalAudit.objects.create(
        application=client, action=action, target_id=target, external_id=external, result=code
    )
    response = envelope(status, code, message, data)
    if code == "RATE_LIMITED":
        response["Retry-After"] = "600"
    return response


def authorized_now(user, client):
    """The account has just signed in to the caller through OIDC: it holds a live access token."""
    return AccessToken.objects.filter(user=user, application=client, expires__gt=timezone.now()).exists()


@service_endpoint("allow_external_link", external=True)
def external_lookup(request, client, body):
    target = body.get("id")
    # withExternalId is opt-in so that the answer stays byte-for-byte the same for older clients.
    with_external = body.get("withExternalId") is True
    if (
        set(body) - {"withExternalId"} != {"id"}
        or ("withExternalId" in body and not with_external)
        or not isinstance(target, str)
        or not OURTAIKO_ID.fullmatch(target)
    ):
        return external_result(client, "lookup", 400, "REQUEST_INVALID", "需要 32 位小写十六进制的 id")
    app_ok = allow_attempt(f"external-lookup-app:{client.pk}", 600)
    id_ok = allow_attempt(f"external-lookup-id:{client.pk}:{target}", 60)
    if not (app_ok and id_ok):
        return external_result(client, "lookup", 429, "RATE_LIMITED", "查询过于频繁", target=target)
    user = User.objects.filter(pk=target, is_active=True).first()
    # Absent, disabled and not yours to see are deliberately the same answer.
    link = ExternalLink.objects.filter(application=client, user=user).first() if user else None
    if user is None or not (link or authorized_now(user, client)):
        return external_result(client, "lookup", 404, "NOT_FOUND", "账号不存在或无权查看", target=target)
    if not user.email_verified or not user.email:
        return external_result(client, "lookup", 409, "EMAIL_UNVERIFIED", "账号邮箱未验证", target=target)
    data = {"id": user.pk, "nickname": user.nickname, "email": user.email, "emailVerified": True}
    if with_external:
        data["externalId"] = link.external_id if link else None
    return external_result(client, "lookup", 200, "OK", data=data, target=target)


@service_endpoint("allow_external_link", external=True)
def external_link(request, client, body):
    target, external = body.get("id"), body.get("externalId")
    if (
        set(body) != {"id", "externalId"}
        or not isinstance(target, str)
        or not OURTAIKO_ID.fullmatch(target)
        or not isinstance(external, str)
        or not EXTERNAL_ID.fullmatch(external)
    ):
        return external_result(client, "link", 400, "REQUEST_INVALID", "需要 id 与 externalId")
    ids = {"target": target, "external": external}
    if not allow_attempt(f"external-link-app:{client.pk}", 120):
        return external_result(client, "link", 429, "RATE_LIMITED", "绑定过于频繁", **ids)

    def linked(link):
        data = {"id": link.user_id, "externalId": link.external_id, "linkedAt": link.created_at.isoformat()}
        return external_result(client, "link", 200, "OK", data=data, **ids)

    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=target, is_active=True).first()
        current = ExternalLink.objects.filter(application=client, user=user).first() if user else None
        # Retrying the same pair succeeds even after the access token expired.
        if current and current.external_id == external:
            return linked(current)
        # Without a fresh sign-in, do not reveal whether the account is bound.
        if user is None or not authorized_now(user, client):
            return external_result(client, "link", 404, "NOT_FOUND", "账号不存在或未授权", **ids)
        if current or ExternalLink.objects.filter(application=client, external_id=external).exists():
            return external_result(client, "link", 409, "ALREADY_LINKED", "任意一边已绑定其他账号", **ids)
        try:
            with transaction.atomic():
                link = ExternalLink.objects.create(application=client, user=user, external_id=external)
        except IntegrityError:
            # Another account bound this external ID concurrently; the user row is locked, so it
            # cannot have been this account.
            return external_result(client, "link", 409, "ALREADY_LINKED", "任意一边已绑定其他账号", **ids)
    return linked(link)
