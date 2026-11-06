"""Viewer RP: verified login, opt-in signup and session-bound account linking.

Authlib owns state, nonce, PKCE and ID Token verification. Cross-platform hints
never grant a local login; existing accounts require independent control proof.
"""
import re
import time
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from oauth2_provider.models import Application

from .forms import PartnerSignupForm
from .models import ExternalLink, User, validate_nickname
from .sakura_oidc_identity import (
    ViewerAccountDisabled,
    ViewerBindingConflict,
    ViewerBindingMissing,
    bound_ourtaiko_user,
    verified_viewer_sub,
)
from .security import allow_attempt

VIEWER_ISSUER = "https://viewer.sakura-bot.cn/oidc"
VIEWER_DISCOVERY = VIEWER_ISSUER + "/.well-known/openid-configuration"
VIEWER_ORIGIN = "https://viewer.sakura-bot.cn"
CALLBACK = "https://sso.ourtaiko.org/login/sakura/callback/"
# A verified Viewer identity waiting for the person to finish the sign-up page.
SIGNUP_SESSION_KEY = "sakura_signup"
SIGNUP_SECONDS = 600
LINK_SESSION_KEY = "sakura_link"
RECOVERY_SESSION_KEY = "sakura_recovery"


def _client():
    if not getattr(settings, "SAKURA_REVERSE_OIDC_ENABLED", False):
        raise Http404
    client_id = getattr(settings, "SAKURA_REVERSE_OIDC_CLIENT_ID", "")
    client_secret = getattr(settings, "SAKURA_REVERSE_OIDC_CLIENT_SECRET", "")
    if client_id != "OurTaiko" or not isinstance(client_secret, str) or len(client_secret) < 32:
        raise Http404

    from authlib.integrations.django_client import OAuth
    from authlib.integrations.django_client.apps import DjangoOAuth2App
    from authlib.integrations.requests_client import OAuth2Session

    class ViewerSession(OAuth2Session):
        def request(self, method, url, **kwargs):
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != "viewer.sakura-bot.cn"
                    or not parsed.path.startswith("/oidc/") or parsed.fragment
                    or parsed.username or parsed.password):
                raise ValueError("Untrusted Viewer endpoint")
            kwargs["timeout"] = (3.05, 10)
            kwargs["allow_redirects"] = False
            response = super().request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                raise ValueError("Viewer endpoint redirected")
            return response

    class ViewerClient(DjangoOAuth2App):
        client_cls = ViewerSession

    oauth = OAuth()
    client = oauth.register(
        name="viewer_oidc",
        client_cls=ViewerClient,
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
        if (parsed.scheme != "https" or parsed.netloc != urlsplit(VIEWER_ORIGIN).netloc
                or not parsed.path.startswith("/oidc/") or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError("Untrusted Viewer OIDC endpoint")
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise ValueError("Viewer OIDC requires S256")
    if "RS256" not in metadata.get("id_token_signing_alg_values_supported", []):
        raise ValueError("Viewer OIDC requires RS256")
    # Authlib uses this allowlist for ID Token JWT algorithm verification.
    metadata["id_token_signing_alg_values_supported"] = ["RS256"]
    return client


def _notice(request, text, *, status=400, confirm=False):
    response = render(request, "accounts/notice.html", {
        "title": "Sakura 授权登录",
        "text": text,
        "confirm": confirm,
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
        return redirect("connections" if settings.SAKURA_REVERSE_LINK_ENABLED else "profile")
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
    state = request.GET.get("state", "")
    link_intent = request.session.pop(LINK_SESSION_KEY + ":" + state, None)
    if link_intent:
        if not settings.SAKURA_REVERSE_LINK_ENABLED or not _same_link_session(request, link_intent):
            return _notice(request, _("登录状态已变化，请重新登录后发起关联。"), status=409)
    elif request.user.is_authenticated:
        return _notice(request, "当前 OurTaiko 登录状态已变化，请退出后重新发起授权。", status=409)
    try:
        client = _client()
        if request.GET.get("error"):
            state = request.GET.get("state")
            if not state or not client.framework.get_state_data(request.session, state):
                raise ValueError("Missing browser-bound authorization state")
            client.framework.clear_state_data(request.session, state)
            if request.GET["error"] == "access_denied":
                if link_intent:
                    # Still signed in to OurTaiko; nothing changed, so go back to where linking began.
                    messages.info(request, _("你已取消 Viewer 授权，关联状态没有变化。"))
                    return redirect("connections")
                return _notice(request, "你已取消 Viewer 授权，可以重新选择登录方式。", status=400)
            raise ValueError("Provider rejected authorization")
        # Fully validate state/nonce and ID Token signature, issuer, audience
        # and lifetime with the maintained client. We do NOT inspect a raw JWT.
        token = client.authorize_access_token(request, leeway=30, claims_options={
            "iss": {"essential": True, "value": VIEWER_ISSUER},
            "sub": {"essential": True}, "aud": {"essential": True},
            "exp": {"essential": True}, "iat": {"essential": True},
            "nonce": {"essential": True},
        })
        claims = token.get("userinfo")
        if not token.get("id_token") or claims is None or not isinstance(claims.get("sub"), str):
            raise ValueError("Verified ID Token is required")
        # Second-layer checks after Authlib's JWKS/nonce/issuer/audience
        # and expiry verification; not a replacement for that verification.
        audience = claims.get("aud")
        if claims.get("iss") != VIEWER_ISSUER or not (
            audience == "OurTaiko" or (isinstance(audience, list) and "OurTaiko" in audience)
        ):
            raise ValueError("Unexpected Viewer token audience or issuer")
        if link_intent:
            return _complete_user_link(request, claims, link_intent)
        user = bound_ourtaiko_user(claims["sub"], application_id="Sakura")
    except ViewerBindingConflict:
        return _notice(request, "账号关联存在冲突，请联系 OurTaiko 管理员。", status=409)
    except ViewerAccountDisabled:
        return _notice(request, "账号不可用。", status=403)
    except ViewerBindingMissing:
        if settings.SAKURA_REVERSE_SIGNUP_ENABLED or (settings.SAKURA_REVERSE_LINK_ENABLED and "ourtaiko_sub" in claims):
            return _begin_signup(request, claims)
        return _notice(
            request,
            "此 Viewer 账号尚未与 OurTaiko 正式关联。请先在 Viewer 的个人资料页关联 OurTaiko，再返回登录。",
            status=409,
        )
    except Exception:
        # No access token, ID token, auth code or provider error text is logged.
        return _notice(request, "Sakura 授权验证未完成，请重新发起登录。", status=400)

    if not user.is_active:
        return _notice(request, "账号不可用。", status=403)
    # Existing, confirmed mapping only. No auto-provisioning or email merge.
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("overview")


def _begin_signup(request, claims):
    """A Viewer account with no OurTaiko pair may create one (account policy §3.1).

    Nothing is written yet: the verified identity waits in the session for the sign-up page.
    """
    # Viewer already pairs this account with an OurTaiko one; that claim only ever blocks.
    if "ourtaiko_sub" in claims:
        hint = claims.get("ourtaiko_sub")
        confirm = bool(settings.SAKURA_REVERSE_LINK_ENABLED and isinstance(hint, str) and re.fullmatch(r"[a-f0-9]{32}", hint))
        if confirm:
            request.session[RECOVERY_SESSION_KEY] = {"user": hint, "sub": verified_viewer_sub(claims["sub"]), "expires": int(time.time()) + 600}
        return _notice(
            request,
            _("这个 Viewer 账号已经关联了一个 OurTaiko 账号。请先用 OurTaiko 登录名和密码登录，再完成关联。"),
            status=409, confirm=confirm,
        )
    email = claims.get("email")
    if claims.get("email_verified") is not True or not isinstance(email, str) or not email.strip():
        return _notice(
            request, _("你的 Viewer 邮箱还没有验证。请先在 Viewer 完成邮箱验证，再回来登录。"), status=409
        )
    email = email.strip().lower()
    try:
        validate_email(email)
        if len(email) > 254:
            raise ValidationError("too long")
    except ValidationError:
        return _notice(request, "Sakura 授权验证未完成，请重新发起登录。", status=400)
    if User.objects.filter(email__iexact=email).exists():
        return _notice(
            request,
            _("这个邮箱已经有 OurTaiko 账号。请先登录原账号，再关联 Sakura Viewer。"),
            status=409,
        )
    nickname = claims.get("name") or claims.get("preferred_username") or ""
    nickname = nickname.strip()[:40] if isinstance(nickname, str) else ""
    try:
        validate_nickname(nickname)
    except ValidationError:
        nickname = ""
    request.session[SIGNUP_SESSION_KEY] = {
        "sub": claims["sub"],
        "email": email,
        "nickname": nickname,
        "expires": int(time.time()) + SIGNUP_SECONDS,
    }
    return redirect("sakura-oidc-signup")


@never_cache
@require_http_methods(["GET", "POST"])
def sakura_signup(request):
    if not (settings.SAKURA_REVERSE_OIDC_ENABLED and settings.SAKURA_REVERSE_SIGNUP_ENABLED):
        raise Http404
    if request.user.is_authenticated:
        return redirect("profile")
    pending = request.session.get(SIGNUP_SESSION_KEY)
    if not pending or pending.get("expires", 0) < time.time():
        request.session.pop(SIGNUP_SESSION_KEY, None)
        return _notice(request, _("注册已超时，请重新使用 Sakura Viewer 登录。"))
    form = PartnerSignupForm(request.POST or None, initial={"nickname": pending["nickname"]})
    if request.method == "POST":
        if not allow_attempt("signup:" + request.META.get("REMOTE_ADDR", ""), 8, 3600):
            form.add_error(None, _("注册次数过多，请一小时后再试。"))
        elif form.is_valid():
            return _create_signup(request, form, pending)
    # Keep the site's same-origin Referrer-Policy: under no-referrer browsers post this form with
    # "Origin: null", which the CSRF check rejects.
    return render(request, "accounts/sakura_signup.html", {"form": form, "email": pending["email"]})


def _create_signup(request, form, pending):
    """Create the account and its pair in one transaction; re-check what may have changed."""
    try:
        with transaction.atomic():
            application = Application.objects.get(client_id="Sakura")
            if ExternalLink.objects.filter(application=application, external_id=pending["sub"]).exists():
                request.session.pop(SIGNUP_SESSION_KEY, None)
                return _notice(request, _("这个 Viewer 账号已经关联了 OurTaiko 账号，请重新使用 Sakura Viewer 登录。"), status=409)
            if User.objects.filter(email__iexact=pending["email"]).exists():
                request.session.pop(SIGNUP_SESSION_KEY, None)
                return _notice(
                    request,
                    _("这个邮箱已经有 OurTaiko 账号。请先登录原账号，再关联 Sakura Viewer。"),
                    status=409,
                )
            user = form.save(commit=False)
            user.email = pending["email"]
            user.email_verified = True
            user.preferred_language = request.LANGUAGE_CODE
            user.set_unusable_password()
            user.save()
            ExternalLink.objects.create(
                application=application,
                user=user,
                external_id=pending["sub"],
                origin=ExternalLink.Origin.SIGNUP,
            )
    except Application.DoesNotExist:
        return _notice(request, "Viewer 授权服务暂不可用，请稍后重试。", status=503)
    except IntegrityError:
        form.add_error(None, _("登录名或邮箱已被使用。"))
        return render(request, "accounts/sakura_signup.html", {"form": form, "email": pending["email"]})
    request.session.pop(SIGNUP_SESSION_KEY, None)
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("overview")


def _same_link_session(request, intent):
    return (request.user.is_authenticated and request.user.is_active
            and intent.get("expires", 0) > time.time()
            and intent.get("user") == request.user.pk
            and intent.get("session") == request.session.session_key
            and intent.get("auth_version") == request.user.auth_version
            and constant_time_compare(intent.get("auth_hash", ""), request.user.get_session_auth_hash()))


@never_cache
@login_required
@require_POST
def start_sakura_link(request):
    if not (settings.SAKURA_REVERSE_OIDC_ENABLED and settings.SAKURA_REVERSE_LINK_ENABLED):
        raise Http404
    hint = request.session.get(RECOVERY_SESSION_KEY)
    if hint and hint.get("expires", 0) <= time.time():
        request.session.pop(RECOVERY_SESSION_KEY, None)
        hint = None
    if hint and hint.get("user") != request.user.pk:
        return _notice(request, _("账号关联存在冲突，请使用原账号或联系支持。"), status=409)
    if not allow_attempt("sakura-link:" + request.user.pk, 20, 600):
        return _notice(request, _("操作过于频繁，请稍后重试。"), status=429)
    try:
        response = _client().authorize_redirect(request, CALLBACK)
        state = parse_qs(urlsplit(response["Location"]).query)["state"][0]
        # One bounded intent per browser; Authlib still validates its own state.
        for key in list(request.session.keys()):
            if key.startswith(LINK_SESSION_KEY + ":"):
                del request.session[key]
        request.session[LINK_SESSION_KEY + ":" + state] = {
            "user": request.user.pk, "session": request.session.session_key,
            "auth_hash": request.user.get_session_auth_hash(), "auth_version": request.user.auth_version,
            "expires": int(time.time()) + 600, "sub": hint.get("sub") if hint else None,
        }
        response["Cache-Control"] = "no-store"
        return response
    except Exception:
        return _notice(request, _("Viewer 授权服务暂不可用，请稍后重试。"), status=503)


def _complete_user_link(request, claims, intent):
    sub = verified_viewer_sub(claims["sub"])
    if ((intent.get("sub") and intent["sub"] != sub)
            or ("ourtaiko_sub" in claims and claims["ourtaiko_sub"] != request.user.pk)):
        return _notice(request, _("账号关联存在冲突，请使用原账号或联系支持。"), status=409)
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=intent["user"])
            session = Session.objects.select_for_update().filter(
                session_key=intent["session"], expire_date__gt=timezone.now()).first()
            stored = session.get_decoded() if session else {}
            if (not _same_link_session(request, intent) or not user.is_active
                    or user.auth_version != intent["auth_version"]
                    or stored.get("_auth_user_id") != user.pk
                    or not constant_time_compare(stored.get("_auth_user_hash", ""), user.get_session_auth_hash())):
                return _notice(request, _("登录状态已变化，请重新登录后发起关联。"), status=409)
            application = Application.objects.get(client_id="Sakura")
            pairs = list(ExternalLink.objects.select_for_update().filter(application=application).filter(
                Q(user=user) | Q(external_id=sub)))
            if any(p.user_id != user.pk or p.external_id != sub for p in pairs):
                return _notice(request, _("账号关联存在冲突，请使用原账号或联系支持。"), status=409)
            if not pairs:
                ExternalLink.objects.create(application=application, user=user, external_id=sub,
                                            origin=ExternalLink.Origin.USER_LINK)
            # Same pair: preserve the existing signup/partner_link origin.
    except IntegrityError:
        return _notice(request, _("账号关联存在冲突，请使用原账号或联系支持。"), status=409)
    request.session.pop(RECOVERY_SESSION_KEY, None)
    messages.success(request, _("Sakura Viewer 关联已确认。"))
    return redirect("connections")
