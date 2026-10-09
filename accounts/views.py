import smtplib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN, LoginView, PasswordResetConfirmView
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.mail import send_mail
from django.db import IntegrityError, connection, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from oauth2_provider.models import AccessToken, Application, Grant, IDToken, RefreshToken

from . import avatars
from .forms import AvatarForm, EmailForm, LoginForm, ProfileForm, RegistrationForm, VerifiedPasswordResetForm
from .models import BrowserSession, GameSession, User
from .security import allow_attempt, revoke_user_tokens


@require_POST
def set_language(request):
    language = request.POST.get("language")
    if language not in User.Language.values:
        return JsonResponse({"code": "LANGUAGE_INVALID", "message": _("不支持此语言。")}, status=400)
    if request.user.is_authenticated:
        User.objects.filter(pk=request.user.pk).update(preferred_language=language)
    request.session["preferred_language"] = language
    next_url = request.POST.get("next", "/")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = "/"
    return redirect(next_url)


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = False


def notice(request, title, text):
    return render(request, "accounts/notice.html", {"title": title, "text": text})


def send_verification(user):
    token = signing.dumps(
        {"id": user.pk, "proof": default_token_generator.make_token(user)}, salt="verify-email"
    )
    url = settings.SSO_BASE_URL + "/verify/" + token + "/"
    with translation.override(user.preferred_language):
        send_mail(
            str(_("验证你的 OurTaiko 邮箱")),
            _("请打开下方链接并确认邮箱（30 分钟内有效）：\n\n%(url)s\n") % {"url": url},
            None,
            [user.email],
        )


@sensitive_post_parameters("password1", "password2")
def register(request):
    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        if not allow_attempt("signup:" + request.META.get("REMOTE_ADDR", ""), 8, 3600):
            form.add_error(None, _("注册次数过多，请一小时后再试。"))
        elif form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save(commit=False)
                    user.preferred_language = request.LANGUAGE_CODE
                    user.save()
                    send_verification(user)
            except IntegrityError:
                form.add_error(None, _("登录名或邮箱已被使用。"))
            except (OSError, smtplib.SMTPException):
                # Mail provider details and user addresses must not appear in a public response.
                form.add_error(None, _("验证邮件暂时无法发送，请稍后再试。"))
            else:
                return notice(
                    request,
                    _("去邮箱看看吧"),
                    _("验证邮件已发送。完成邮箱验证后，就可以登录你的 OurTaiko 账号。"),
                )
    return render(request, "accounts/register.html", {"form": form})


def verify_email(request, token):
    user = None
    try:
        payload = signing.loads(token, salt="verify-email", max_age=1800)
        user = User.objects.get(pk=payload["id"], is_active=False, email_verified=False)
        if not default_token_generator.check_token(user, payload["proof"]):
            user = None
    except (signing.BadSignature, User.DoesNotExist, KeyError):
        pass
    if not user:
        return notice(request, _("链接已失效"), _("这封邮件的验证链接已使用或已过期。请重新获取验证邮件。"))
    if request.method == "POST":
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            if not default_token_generator.check_token(locked, payload["proof"]) or locked.is_active:
                return notice(request, _("链接已失效"), _("请重新获取验证邮件。"))
            locked.is_active = locked.email_verified = True
            locked.save()
        messages.success(request, _("邮箱验证完成，现在可以登录了。"))
        return redirect("login")
    return render(request, "accounts/verify.html", {"email": user.email})


def resend_verification(request):
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not allow_attempt("resend:" + request.META.get("REMOTE_ADDR", ""), 5, 3600):
            form.add_error(None, _("发送次数过多，请稍后再试。"))
        else:
            user = User.objects.filter(
                email__iexact=form.cleaned_data["email"], is_active=False, email_verified=False
            ).first()
            if user:
                send_verification(user)
            return notice(
                request, _("请查看你的邮箱"), _("如果该邮箱有待验证的账号，我们已发送新的验证邮件。")
            )
    return render(
        request,
        "accounts/form.html",
        {"form": form, "title": _("重新发送验证邮件"), "submit": _("发送验证邮件")},
    )


@login_required
def overview(request):
    games = GameSession.objects.filter(user=request.user, expires_at__gt=timezone.now())
    apps = AccessToken.objects.filter(user=request.user, expires__gt=timezone.now())
    return render(
        request,
        "accounts/overview.html",
        {
            "active": "overview",
            "connection_count": len(set(games.values_list("client_id", flat=True)))
            + len(set(apps.values_list("application_id", flat=True))),
        },
    )


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        # Only update editable fields; never overwrite concurrent password changes.
        User.objects.filter(pk=request.user.pk).update(
            nickname=form.cleaned_data["nickname"],
            preferred_language=form.cleaned_data["preferred_language"],
        )
        translation.activate(form.cleaned_data["preferred_language"])
        messages.success(request, _("个人资料已更新。"))
        return redirect("profile")
    return render(
        request, "accounts/profile.html", {"form": form, "avatar_form": AvatarForm(), "active": "profile"}
    )


@login_required
@require_POST
def profile_avatar(request):
    if request.POST.get("action") == "remove":
        avatars.clear(User, request.user.pk)
        messages.success(request, _("头像已移除。"))
        return redirect("profile")
    avatar_form = AvatarForm(
        request.POST, request.FILES, too_large=getattr(request, "upload_too_large", False)
    )
    if not allow_attempt("avatar:" + request.user.pk, 20, 3600):
        avatar_form.add_error(None, _("上传次数过多，请稍后再试。"))
    elif avatar_form.is_valid():
        try:
            avatars.replace(User, request.user.pk, avatar_form.cleaned_data["webp"])
        except Exception:
            avatars.logger.exception("Avatar upload failed")
            avatar_form.add_error(None, _("头像暂时无法保存，请稍后再试。"))
        else:
            messages.success(request, _("头像已更新。"))
            return redirect("profile")
    form = ProfileForm(instance=request.user)
    return render(
        request,
        "accounts/profile.html",
        {"form": form, "avatar_form": avatar_form, "active": "profile"},
        status=400,
    )


def avatar(request, user_id, digest):
    name = f"avatars/{user_id}/{digest}.webp"
    if not User.objects.filter(pk=user_id, is_active=True, avatar=name).exists():
        raise Http404
    if settings.AVATAR_CDN_BASE_URL:
        response = redirect(f"{settings.AVATAR_CDN_BASE_URL}/{user_id}/{digest}.webp")
        response["Cache-Control"] = "no-store"
        return response
    try:
        with avatars.storage().open(name) as file:
            data = file.read()
    except FileNotFoundError:
        raise Http404 from None
    except Exception:
        avatars.logger.exception("Avatar read failed")
        return HttpResponse(status=503)
    response = HttpResponse(data, content_type="image/webp")
    # The URL is content-addressed, so it can be cached forever by browsers and CDNs.
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response.public_cache = True
    return response


@login_required
@sensitive_post_parameters("old_password", "new_password1", "new_password2")
def security(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST":
        if not allow_attempt("password-change:" + request.user.pk, 10):
            form.add_error(None, _("尝试次数过多，请稍后再试。"))
        elif form.is_valid():
            with transaction.atomic():
                locked = User.objects.select_for_update().get(pk=request.user.pk)
                if not locked.is_active or locked.password != request.user.password:
                    form.add_error(None, _("账号状态已改变，请刷新后重试。"))
                    return render(request, "accounts/security.html", {"form": form, "active": "security"})
                form.user = locked
                form.save()  # Signals revoke OAuth and game sessions; Django invalidates other browser sessions.
                request.user = locked
            old_key = request.session.session_key
            update_session_auth_hash(request, request.user)
            BrowserSession.objects.filter(pk=old_key).delete()
            BrowserSession.objects.update_or_create(
                session_key=request.session.session_key,
                defaults={"user": request.user, "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200]},
            )
            messages.success(request, _("密码已更新，其他设备和游戏登录已失效。当前浏览器保持登录。"))
            return redirect("security")
    return render(request, "accounts/security.html", {"form": form, "active": "security"})


def password_reset(request):
    form = VerifiedPasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not allow_attempt("reset:" + request.META.get("REMOTE_ADDR", ""), 5, 3600):
            form.add_error(None, _("发送次数过多，请一小时后再试。"))
        else:
            from urllib.parse import urlsplit

            # Reset emails use the configured canonical origin, never the incoming Host.
            form.save(
                request=request,
                domain_override=urlsplit(settings.SSO_BASE_URL).netloc,
                use_https=settings.SSO_BASE_URL.startswith("https://"),
                email_template_name="accounts/reset_email.txt",
                subject_template_name="accounts/reset_subject.txt",
            )
            return notice(
                request,
                _("请查看你的邮箱"),
                _("如果该邮箱绑定了可用账号，我们已发送密码重置链接。链接 30 分钟内有效。"),
            )
    return render(
        request,
        "accounts/form.html",
        {
            "form": form,
            "title": _("找回密码"),
            "description": _("输入注册邮箱，我们会发送重置密码的链接。"),
            "submit": _("发送重置链接"),
        },
    )


class ResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/reset_confirm.html"
    success_url = reverse_lazy("login")

    def form_valid(self, form):
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=form.user.pk)
            if (
                not locked.is_active
                or not locked.email_verified
                or not self.token_generator.check_token(
                    locked, self.request.session.get(INTERNAL_RESET_SESSION_TOKEN)
                )
            ):
                self.validlink = False
                return self.render_to_response(self.get_context_data())
            form.user = locked
            response = super().form_valid(form)
        if self.request.user.is_authenticated and self.request.user.pk == locked.pk:
            logout(self.request)
        messages.success(self.request, _("密码已重置，请使用新密码登录。"))
        return response


@login_required
def sessions(request):
    live_keys = Session.objects.filter(expire_date__gt=timezone.now()).values("session_key")
    browsers = BrowserSession.objects.filter(user=request.user, session_key__in=live_keys).order_by(
        "-created_at"
    )
    games = GameSession.objects.filter(user=request.user, expires_at__gt=timezone.now()).select_related(
        "client"
    )
    return render(
        request,
        "accounts/sessions.html",
        {
            "active": "sessions",
            "browsers": browsers,
            "games": games,
            "current_key": request.session.session_key,
        },
    )


@login_required
@require_POST
def revoke_session(request):
    kind, key = request.POST.get("kind"), request.POST.get("key")
    if kind == "browser":
        owned = BrowserSession.objects.filter(user=request.user, pk=key)
        if owned.exists():
            Session.objects.filter(pk=key).delete()
            owned.delete()
            if key == request.session.session_key:
                logout(request)
                return redirect("login")
    elif kind == "game":
        GameSession.objects.filter(user=request.user, pk=key).delete()
    else:
        raise Http404
    messages.success(request, _("该登录已撤销。"))
    return redirect("sessions")


@login_required
@require_POST
def revoke_all(request):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        user.auth_version += 1
        user.save(update_fields=["auth_version"])
        revoke_user_tokens(user)
        Session.objects.filter(
            session_key__in=BrowserSession.objects.filter(user=user).values("session_key")
        ).delete()
        BrowserSession.objects.filter(user=user).delete()
    logout(request)
    messages.success(request, _("所有登录和授权凭证已撤销，请重新登录。"))
    return redirect("login")


@login_required
def connections(request):
    apps = Application.objects.filter(
        accesstoken__user=request.user, accesstoken__expires__gt=timezone.now()
    ).distinct()
    games = (
        GameSession.objects.filter(user=request.user, expires_at__gt=timezone.now())
        .values("client_id", "client__name")
        .distinct()
    )
    return render(
        request, "accounts/connections.html", {"active": "connections", "apps": apps, "game_apps": games}
    )


@login_required
@require_POST
def revoke_connection(request):
    key = request.POST.get("key")
    if request.POST.get("kind") == "game" and str(key).isdigit():
        GameSession.objects.filter(user=request.user, client_id=key).delete()
    elif request.POST.get("kind") == "web" and str(key).isdigit():
        for model in [RefreshToken, AccessToken, IDToken, Grant]:
            model.objects.filter(user=request.user, application_id=key).delete()
    else:
        raise Http404
    messages.success(request, _("应用授权已撤销。"))
    return redirect("connections")


def health(request):
    return JsonResponse({"ok": True})


def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"ok": False}, status=503)
    return JsonResponse({"ok": True})
