"""OurTaiko as a Relying Party of the future Viewer OIDC Provider.

Existing formal pairs only in this initial stage. We deliberately do not
provision users, merge by email, accept Viewer passwords, or write a second link.
The Authlib Django client owns state, nonce, PKCE and ID Token verification.
Feature is DISABLED unless privately configured.
"""
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import login as django_login
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .sakura_oidc_identity import (
    ViewerBindingMissing, ViewerSubjectInvalid, bound_ourtaiko_user,
)

VIEWER_ISSUER = "https://viewer.sakura-bot.cn/oidc"
VIEWER_DISCOVERY = VIEWER_ISSUER + "/.well-known/openid-configuration"
VIEWER_ORIGIN = "https://viewer.sakura-bot.cn"
CALLBACK = "https://sso.ourtaiko.org/login/sakura/callback/"


def _client():
    if not getattr(settings, "SAKURA_REVERSE_OIDC_ENABLED", False):
        raise Http404
    client_id = getattr(settings, "SAKURA_REVERSE_OIDC_CLIENT_ID", "")
    client_secret = getattr(settings, "SAKURA_REVERSE_OIDC_CLIENT_SECRET", "")
    if client_id != "OurTaiko" or not isinstance(client_secret, str) or len(client_secret) < 24:
        raise Http404

    # Deferred import: does not affect running OurTaiko until separately enabled.
    # The dependency and its production lock must be installed before activation.
    from authlib.integrations.django_client import OAuth

    oauth = OAuth()
    client = oauth.register(
        name="viewer_oidc",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=VIEWER_DISCOVERY,
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic",
        },
    )
    metadata = client.load_server_metadata()
    if metadata.get("issuer") != VIEWER_ISSUER:
        raise ValueError("Unexpected Viewer issuer")
    # Fail closed if a discovery result attempts to redirect a confidential
    # client secret, auth code, or JWKS request to a different origin.
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint"):
        target = metadata.get(field)
        if not isinstance(target, str):
            raise ValueError("Incomplete Viewer OIDC discovery")
        parsed = urlsplit(target)
        if parsed.scheme != "https" or parsed.netloc != urlsplit(VIEWER_ORIGIN).netloc or parsed.username or parsed.password:
            raise ValueError("Untrusted Viewer OIDC endpoint")
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise ValueError("Viewer OIDC requires S256")
    if "RS256" not in metadata.get("id_token_signing_alg_values_supported", []):
        raise ValueError("Viewer OIDC requires RS256")
    return client


def _notice(request, text, *, status=400):
    response = render(request, "accounts/notice.html", {
        "title": "Sakura 授权登录",
        "text": text,
    }, status=status)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_GET
def start_sakura_login(request):
    if not getattr(settings, "SAKURA_REVERSE_OIDC_ENABLED", False):
        raise Http404
    if request.user.is_authenticated:
        # Do not replace an existing OurTaiko session or silently bind identities.
        return redirect("profile")
    try:
        client = _client()
        # Authlib stores the browser-bound state, nonce, and PKCE verifier in
        # Django's server-side session and validates them on the callback.
        response = client.authorize_redirect(request, CALLBACK)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
    except Exception:
        return _notice(request, "Viewer 授权服务暂不可用，请稍后重试。", status=503)


@never_cache
@require_GET
def sakura_callback(request):
    if not getattr(settings, "SAKURA_REVERSE_OIDC_ENABLED", False):
        raise Http404
    if request.user.is_authenticated:
        return _notice(request, "当前 OurTaiko 登录状态已变化，请退出后重新发起授权。", status=409)
    try:
        client = _client()
        # Fully validate state/nonce and ID Token signature, issuer, audience
        # and lifetime with the maintained client. We do NOT inspect a raw JWT.
        token = client.authorize_access_token(request)
        claims = token.get("userinfo")
        if claims is None or not isinstance(claims.get("sub"), str):
            raise ValueError("OIDC claims unavailable")
        user = bound_ourtaiko_user(claims["sub"], application_id="Sakura")
    except ViewerBindingMissing:
        return _notice(
            request,
            "此 Viewer 账号尚未与 OurTaiko 正式关联。请先在 Viewer 的个人资料页关联 OurTaiko，再返回登录。",
            status=409,
        )
    except (ViewerSubjectInvalid, Exception):
        # No access token, ID token, auth code or provider error text is logged.
        return _notice(request, "Sakura 授权验证未完成，请重新发起登录。", status=400)

    if not user.is_active:
        return _notice(request, "账号不可用。", status=403)
    # Existing, confirmed mapping only. No auto-provisioning or email merge.
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("overview")
