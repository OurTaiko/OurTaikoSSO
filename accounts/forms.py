from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from .models import User
from .security import login_allowed


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


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["nickname", "preferred_language"]
        labels = {"nickname": _("昵称")}
        help_texts = {"nickname": _("最多 40 个字符。修改后，接入的服务会在缓存更新后显示新昵称。")}

    def clean_nickname(self):
        return self.cleaned_data["nickname"].strip()


class EmailForm(forms.Form):
    email = forms.EmailField(label=_("注册邮箱"), max_length=254)


class VerifiedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        return (user for user in super().get_users(email) if user.email_verified)

    def send_mail(self, subject_template_name, email_template_name, context, *args, **kwargs):
        # Background/email rendering follows the recipient, not the requesting browser.
        with translation.override(context["user"].preferred_language):
            return super().send_mail(subject_template_name, email_template_name, context, *args, **kwargs)
