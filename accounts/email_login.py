"""Sign in with a one-time code sent to a verified mailbox.

The browser asks for a code with an email address. The code waits, hashed, in the server-side
session for ten minutes and is checked at most five times. Whether an address belongs to an
account is never revealed: the code page is shown either way, and mail is only sent when a usable
account exists. Accounts created through a partner platform have no password; this is how they
sign in with a login name-free flow.
"""

import secrets
import smtplib
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as django_login
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .forms import EmailCodeForm, EmailLoginForm
from .models import User
from .security import allow_attempt

SESSION_KEY = "email_login"
CODE_SECONDS = 600
MAX_ATTEMPTS = 5


def _require_enabled():
    if not settings.EMAIL_CODE_LOGIN_ENABLED:
        raise Http404


def _safe_next(request, value):
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return ""


def _code_hash(code, email):
    return salted_hmac("accounts.email_login", email.casefold() + ":" + code).hexdigest()


def _usable_account(email):
    users = User.objects.filter(email__iexact=email, is_active=True, email_verified=True)
    return next((user for user in users if user.email.casefold() == email.casefold()), None)


def _send_code(user, code):
    # Background/email rendering follows the recipient, not the requesting browser.
    with translation.override(user.preferred_language):
        send_mail(
            str(_("你的 OurTaiko 登录验证码")),
            _(
                "你的 OurTaiko 登录验证码是：%(code)s\n\n"
                "验证码 10 分钟内有效。如果不是你在登录，请忽略这封邮件，不要把验证码告诉任何人。\n"
            )
            % {"code": code},
            None,
            [user.email],
        )


@never_cache
def start(request):
    _require_enabled()
    source = request.POST if request.method == "POST" else request.GET
    next_url = _safe_next(request, source.get("next"))
    form = EmailLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        address_ok = allow_attempt("email-login-ip:" + request.META.get("REMOTE_ADDR", ""), 10, 3600)
        mailbox_ok = allow_attempt("email-login-email:" + email.casefold()[:200], 5, 3600)
        if not (address_ok and mailbox_ok):
            form.add_error(None, _("发送次数过多，请稍后再试。"))
        else:
            user = _usable_account(email)
            code = f"{secrets.randbelow(10**6):06d}"
            try:
                if user:
                    _send_code(user, code)
            except (OSError, smtplib.SMTPException):
                # Mail provider details and user addresses must not appear in a public response.
                form.add_error(None, _("验证码邮件暂时无法发送，请稍后再试。"))
            else:
                request.session[SESSION_KEY] = {
                    "email": email,
                    "user": user.pk if user else "",
                    "hash": _code_hash(code, email),
                    "expires": int(time.time()) + CODE_SECONDS,
                    "attempts": 0,
                    "next": next_url,
                }
                return redirect("email-login-code")
    return render(request, "accounts/login_email.html", {"form": form, "next": next_url, "mode": "email"})


@never_cache
@sensitive_post_parameters("code")
def verify(request):
    _require_enabled()
    pending = request.session.get(SESSION_KEY)
    if not pending:
        return redirect("email-login")
    if pending["expires"] <= time.time():
        del request.session[SESSION_KEY]
        messages.error(request, _("验证码已过期，请重新获取。"))
        return redirect("email-login")
    form = EmailCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        pending["attempts"] += 1
        request.session[SESSION_KEY] = pending
        if pending["attempts"] > MAX_ATTEMPTS:
            del request.session[SESSION_KEY]
            messages.error(request, _("尝试次数过多，请重新获取验证码。"))
            return redirect("email-login")
        user = _usable_account(pending["email"]) if pending["user"] else None
        if (
            user
            and user.pk == pending["user"]
            and constant_time_compare(pending["hash"], _code_hash(form.cleaned_data["code"], pending["email"]))
        ):
            next_url = _safe_next(request, pending.get("next"))
            del request.session[SESSION_KEY]
            django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(next_url or settings.LOGIN_REDIRECT_URL)
        form.add_error("code", _("验证码不正确。"))
    return render(request, "accounts/login_code.html", {"form": form, "email": pending["email"], "mode": "email"})
