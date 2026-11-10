import logging

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm
from django.template import loader
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from . import avatars, mail
from .models import User
from .security import login_allowed

logger = logging.getLogger(__name__)


class LoginForm(AuthenticationForm):
    username = forms.CharField(label=_("登录名"), max_length=24)
    password = forms.CharField(
        label=_("密码"), widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def clean(self):
        if not login_allowed(self.request, self.cleaned_data.get("username", "")):
            raise forms.ValidationError(_("尝试次数过多，请 10 分钟后再试。"))
        return super().clean()


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label=_("邮箱"), max_length=254)

    class Meta:
        model = User
        fields = ["username", "nickname", "email", "password1", "password2"]
        labels = {"username": _("登录名"), "nickname": _("昵称")}
        help_texts = {
            "username": _("3–24 位英文字母或数字，用于登录。"),
            "nickname": _("大家在作品和排行榜上看到的名字。"),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("该邮箱已注册，请登录或找回密码。"))
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = False
        if commit:
            user.save()
        return user


class PartnerSignupForm(forms.ModelForm):
    """The two choices a person makes when a partner platform sign-in creates their account."""

    class Meta:
        model = User
        fields = ["username", "nickname"]
        labels = RegistrationForm.Meta.labels
        help_texts = RegistrationForm.Meta.help_texts

    def clean_nickname(self):
        return self.cleaned_data["nickname"].strip()


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["nickname", "preferred_language"]
        labels = {"nickname": _("昵称")}
        help_texts = {"nickname": _("最多 40 个字符。修改后，接入的服务会在缓存更新后显示新昵称。")}

    def clean_nickname(self):
        return self.cleaned_data["nickname"].strip()


class AvatarForm(forms.Form):
    avatar = forms.FileField(
        label=_("上传新头像"),
        help_text=_("支持 JPG、JPEG、PNG、WebP，最大 5 MB。图片会裁成正方形并转换为 WebP。"),
        widget=forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )

    def __init__(self, *args, too_large=False, **kwargs):
        super().__init__(*args, **kwargs)
        # UploadLimitMiddleware already discarded the file; report the limit, not "required".
        self.too_large = too_large
        if too_large:
            self.fields["avatar"].required = False

    def clean_avatar(self):
        if self.too_large:
            raise forms.ValidationError(_("图片不能超过 5 MB。"))
        upload = self.cleaned_data["avatar"]
        try:
            self.cleaned_data["webp"] = avatars.to_webp(upload)
        except avatars.AvatarError as error:
            raise forms.ValidationError(str(error)) from None
        return upload


class EmailForm(forms.Form):
    email = forms.EmailField(label=_("注册邮箱"), max_length=254)


class EmailLoginForm(forms.Form):
    email = forms.EmailField(
        label=_("注册邮箱"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )


class EmailCodeForm(forms.Form):
    code = forms.RegexField(
        r"^[0-9]{6}$",
        label=_("验证码"),
        error_messages={"invalid": _("验证码是 6 位数字。")},
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": "6", "autofocus": True}
        ),
    )


class VerifiedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        # Unlike Django's default, accounts without a password (created by signing in through a
        # partner platform) may set one here: the verified mailbox is the proof.
        users = User.objects.filter(email__iexact=email, is_active=True, email_verified=True)
        return (user for user in users if user.email.casefold() == email.casefold())

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, **kwargs):
        # Background/email rendering follows the recipient, not the requesting browser.
        with translation.override(context["user"].preferred_language):
            subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
            body = loader.render_to_string(email_template_name, context)
        try:
            mail.send("reset", subject, body, to_email)
        except Exception:
            # Like Django's PasswordResetForm: a mail failure must not reveal whether the address exists.
            logger.exception("Failed to send password reset email to %s", context["user"].pk)
