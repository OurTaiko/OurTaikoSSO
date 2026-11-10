"""Each kind of mail leaves from its own configured sender (EMAIL_SENDERS)."""

from django.core import mail as outbox
from django.test import SimpleTestCase, TestCase, override_settings

from accounts import mail
from accounts.models import User
from config.settings import email_senders

PASSWORD = "Rhythm!Dev4279"
SENDERS = {
    "verify": {"from_email": "OurTaiko 注册 <verify@example.test>", "username": "verify@example.test", "password": "verify-pw"},
    "login_code": {"from_email": "OurTaiko 登录 <code@example.test>", "username": "code@example.test", "password": "code-pw"},
    "reset": {"from_email": "OurTaiko 安全 <reset@example.test>", "username": "reset@example.test", "password": "reset-pw"},
}


@override_settings(EMAIL_SENDERS=SENDERS)
class MailSenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="player1", nickname="小咚", email="player@example.test", email_verified=True, password=PASSWORD
        )

    def test_verification_mail_leaves_from_the_verify_sender(self):
        self.client.post(
            "/register/",
            {"username": "newPlayer", "nickname": "新人", "email": "new@example.test", "password1": PASSWORD, "password2": PASSWORD},
        )
        self.assertEqual(outbox.outbox[0].from_email, "OurTaiko 注册 <verify@example.test>")
        self.assertEqual(outbox.outbox[0].to, ["new@example.test"])

    def test_sign_in_code_leaves_from_the_login_code_sender(self):
        self.client.post("/login/email/", {"email": self.user.email})
        self.assertEqual(outbox.outbox[0].from_email, "OurTaiko 登录 <code@example.test>")

    def test_password_reset_leaves_from_the_reset_sender(self):
        self.client.post("/password/reset/", {"email": self.user.email})
        self.assertEqual(len(outbox.outbox), 1)
        self.assertEqual(outbox.outbox[0].from_email, "OurTaiko 安全 <reset@example.test>")
        self.assertIn("/password/reset/", outbox.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    def test_connection_logs_in_as_the_sender(self):
        for kind, configured in SENDERS.items():
            connection = mail.connection(kind)
            self.assertEqual((connection.username, connection.password), (configured["username"], configured["password"]))


class EmailSenderSettingsTests(SimpleTestCase):
    def senders(self, **env):
        return email_senders(lambda key, default="": env.get(key, default), "OurTaiko <no-reply@example.test>", "host-user", "host-pw")

    def test_unset_kinds_use_the_shared_account(self):
        for configured in self.senders().values():
            self.assertEqual(configured, {"from_email": "OurTaiko <no-reply@example.test>", "username": "host-user", "password": "host-pw"})

    def test_a_kind_logs_in_as_its_own_address(self):
        senders = self.senders(EMAIL_VERIFY_FROM="OurTaiko 注册 <verify@example.test>", EMAIL_VERIFY_PASSWORD="verify-pw")
        self.assertEqual(senders["verify"], {"from_email": "OurTaiko 注册 <verify@example.test>", "username": "verify@example.test", "password": "verify-pw"})
        self.assertEqual(senders["reset"]["username"], "host-user")

    def test_login_name_and_password_may_be_overridden(self):
        senders = self.senders(EMAIL_RESET_FROM="reset@example.test", EMAIL_RESET_USER="smtp-reset", EMAIL_RESET_PASSWORD="reset-pw")
        self.assertEqual(senders["reset"], {"from_email": "reset@example.test", "username": "smtp-reset", "password": "reset-pw"})

    def test_a_malformed_address_is_refused(self):
        from django.core.exceptions import ImproperlyConfigured

        for bad in ("not an address", "OurTaiko <>", "a@b\nBcc: x@y"):
            with self.assertRaises(ImproperlyConfigured):
                self.senders(EMAIL_LOGIN_CODE_FROM=bad)
