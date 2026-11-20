import re
import secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .api import problem
from .game_accounts import device_name, game_client, game_endpoint, issue_account
from .models import GameAuthorization, User
from .security import allow_attempt, client_ip, digest


@game_endpoint("POST")
def start(request, body):
    if game_client() is None:
        return problem(403, "GAME_DISABLED", "游戏登录暂不可用")
    if not allow_attempt("device-start:" + client_ip(request), 30):
        return problem(429, "RATE_LIMITED", "请稍后重试")
    secret = secrets.token_hex(32)
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    GameAuthorization.objects.create(
        device_hash=digest(secret), code_hash=digest(code), device_name=device_name(body.get("deviceName")),
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    return JsonResponse({"deviceCode": secret, "userCode": code, "expiresIn": 300, "interval": 5})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def approve(request):
    candidate = request.GET.get("code", "")
    context = {"candidate": candidate if re.fullmatch("[A-Z2-9]{8}", candidate) else ""}
    if request.method == "POST":
        if not allow_attempt("device-approve:" + str(request.user.pk), 15):
            return problem(429, "RATE_LIMITED", "请稍后重试")
        code = request.POST.get("code", "").strip().upper()
        pending = GameAuthorization.objects.filter(
            code_hash=digest(code), expires_at__gt=timezone.now(), user__isnull=True, denied=False,
        )
        if request.POST.get("confirm") == "no":
            context["denied" if pending.update(denied=True) else "invalid"] = True
        elif request.POST.get("confirm") == "yes" and game_client() and pending.update(
            user=request.user, auth_version=request.user.auth_version,
        ):
            context["complete"] = True
        elif grant := pending.first():
            context.update(code=code, device=grant.device_name)
        else:
            context["invalid"] = True
    return render(request, "accounts/game_authorize.html", context)


@game_endpoint("POST")
@transaction.atomic
def exchange(request, body):
    secret = body.get("deviceCode")
    if not isinstance(secret, str) or not re.fullmatch("[0-9a-f]{64}", secret):
        return problem(400, "REQUEST_INVALID", "设备凭据格式不正确")
    grants = GameAuthorization.objects.filter(pk=digest(secret))
    grant = grants.filter(expires_at__gt=timezone.now()).first()
    if not grant:
        return problem(410, "DEVICE_EXPIRED", "授权已失效")
    if grant.denied:
        return problem(403, "DEVICE_DENIED", "玩家已拒绝授权")
    if not allow_attempt("device-poll:" + grant.pk, 3, 10):
        response = problem(429, "SLOW_DOWN", "请稍后重试")
        response["Retry-After"] = "10"
        return response
    if not grant.user_id:
        return JsonResponse({"pending": True}, status=202)
    # Revocation also locks the account before removing its grants.
    user = User.objects.select_for_update().filter(pk=grant.user_id).first()
    grant = grants.select_for_update().filter(user_id=grant.user_id).first()
    if not grant or not user or grant.expires_at <= timezone.now():
        return problem(410, "DEVICE_EXPIRED", "授权已失效")
    client = game_client()
    if not client or not user.is_active or user.auth_version != grant.auth_version:
        grant.delete()
        return problem(403, "AUTHORIZATION_INVALID", "授权已失效")
    grant.delete()
    return issue_account(user, client, grant.device_name)
