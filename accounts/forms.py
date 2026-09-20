from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm

from .models import User
from .security import login_allowed


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="登录名", max_length=24)
    password = forms.CharField(
        label="密码", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def clean(self):
        if not login_allowed(self.request, self.cleaned_data.get("username", "")):
            raise forms.ValidationError("尝试次数过多，请 10 分钟后再试。")
        return super().clean()


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label="邮箱", max_length=254)

    class Meta:
        model = User
        fields = ["username", "nickname", "email", "password1", "password2"]
        labels = {"username": "登录名", "nickname": "昵称"}
        help_texts = {
            "username": "3–24 位英文字母或数字，用于登录。",
            "nickname": "大家在作品和排行榜上看到的名字。",
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("该邮箱已注册，请登录或找回密码。")
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
        fields = ["nickname"]
        labels = {"nickname": "昵称"}
        help_texts = {"nickname": "最多 40 个字符。修改后，接入的服务会在缓存更新后显示新昵称。"}

    def clean_nickname(self):
        return self.cleaned_data["nickname"].strip()


class EmailForm(forms.Form):
    email = forms.EmailField(label="注册邮箱", max_length=254)


class VerifiedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        return (user for user in super().get_users(email) if user.email_verified)
