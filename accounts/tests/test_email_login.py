"""Sign in with a one-time code sent to the verified mailbox (/login/email/)."""

import re
import smtplib
import time
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from accounts.email_login import SESSION_KEY
from accounts.models import BrowserSession, User

PASSWORD = "Rhythm!Dev4279"


class EmailLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="player1", nickname="小咚", email="Player@example.test", email_verified=True, password=PASSWORD
        )

    def request_code(self, email="player@example.test", **extra):
        return self.client.post("/login/email/", {"email": email, **extra})

    def sent_code(self):
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.user.email])
        return re.search(r"\b(\d{6})\b", message.body).group(1)

    def logged_in_as(self):
        return self.client.session.get("_auth_user_id")

    def test_login_page_switches_between_password_and_email_code(self):
        page = self.client.get("/login/?next=/sessions/")
        self.assertContains(page, 'href="/login/email/?next=%2Fsessions%2F"')
        self.assertContains(page, "邮箱验证码登录")
        page = self.client.get("/login/email/?next=/sessions/")
        self.assertContains(page, 'href="/login/?next=%2Fsessions%2F"')
        self.assertContains(page, 'name="next" value="/sessions/"')
        self.assertContains(page, "发送验证码")

    def test_code_signs_in_and_honours_next(self):
        response = self.request_code(next="/sessions/")
        self.assertRedirects(response, "/login/email/code/", fetch_redirect_response=False)
        code = self.sent_code()
        self.assertIn("10 分钟", mail.outbox[0].body)
        page = self.client.get("/login/email/code/")
        self.assertContains(page, "player@example.test")
        self.assertContains(page, 'autocomplete="one-time-code"')

        response = self.client.post("/login/email/code/", {"code": code})
        self.assertRedirects(response, "/sessions/", fetch_redirect_response=False)
        self.assertEqual(self.logged_in_as(), self.user.pk)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertTrue(BrowserSession.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)

    def test_account_without_password_can_sign_in(self):
        self.user.set_unusable_password()
        self.user.save()
        self.request_code()
        response = self.client.post("/login/email/code/", {"code": self.sent_code()})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(self.logged_in_as(), self.user.pk)

    def test_mailbox_match_ignores_case_and_code_is_single_use(self):
        self.request_code("PLAYER@EXAMPLE.TEST")
        code = self.sent_code()
        self.client.post("/login/email/code/", {"code": code})
        self.assertEqual(self.logged_in_as(), self.user.pk)
        self.client.logout()
        # The code was removed from the session on success; the page now asks for an email again.
        self.assertRedirects(self.client.post("/login/email/code/", {"code": code}), "/login/email/")

    def test_unknown_unverified_and_disabled_mailboxes_get_no_mail_but_the_same_page(self):
        other = User.objects.create_user(username="player2", nickname="二", email="two@example.test", password=PASSWORD)
        off = User.objects.create_user(
            username="player3", nickname="三", email="three@example.test", email_verified=True, password=PASSWORD
        )
        off.is_active = False
        off.save()
        for email in ["nobody@example.test", other.email, off.email]:
            response = self.request_code(email)
            self.assertRedirects(response, "/login/email/code/", fetch_redirect_response=False)
            page = self.client.get("/login/email/code/")
            self.assertContains(page, email)
            response = self.client.post("/login/email/code/", {"code": "000000"})
            self.assertContains(response, "验证码不正确")
            self.assertIsNone(self.logged_in_as())
        self.assertEqual(mail.outbox, [])

    def test_wrong_code_is_limited_to_five_attempts(self):
        self.request_code()
        code = self.sent_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            response = self.client.post("/login/email/code/", {"code": wrong})
            self.assertContains(response, "验证码不正确")
        response = self.client.post("/login/email/code/", {"code": code})
        self.assertRedirects(response, "/login/email/", fetch_redirect_response=False)
        self.assertIsNone(self.logged_in_as())
        page = self.client.get("/login/email/")
        self.assertContains(page, "尝试次数过多")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_expired_code_is_refused(self):
        self.request_code()
        code = self.sent_code()
        session = self.client.session
        session[SESSION_KEY]["expires"] = int(time.time()) - 1
        session.save()
        response = self.client.post("/login/email/code/", {"code": code})
        self.assertRedirects(response, "/login/email/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/login/email/"), "验证码已过期")
        self.assertIsNone(self.logged_in_as())

    def test_code_page_rejects_malformed_codes_without_counting_them(self):
        self.request_code()
        response = self.client.post("/login/email/code/", {"code": "12ab"})
        self.assertContains(response, "6 位数字")
        self.assertEqual(self.client.session[SESSION_KEY]["attempts"], 0)

    def test_requests_per_mailbox_and_address_are_limited(self):
        for _ in range(5):
            self.assertEqual(self.request_code().status_code, 302)
        response = self.request_code()
        self.assertContains(response, "发送次数过多")
        self.assertEqual(len(mail.outbox), 5)
        for index in range(4):
            self.assertEqual(self.request_code(f"other{index}@example.test").status_code, 302)
        self.assertContains(self.request_code("other9@example.test"), "发送次数过多")

    def test_mail_failure_is_reported_without_details(self):
        with mock.patch("accounts.mail.send", side_effect=smtplib.SMTPException("relay 550 secret-host")):
            response = self.request_code()
        self.assertContains(response, "暂时无法发送")
        self.assertNotContains(response, "secret-host")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_unsafe_next_falls_back_to_home(self):
        self.request_code(next="https://evil.example/")
        response = self.client.post("/login/email/code/", {"code": self.sent_code()})
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_email_is_rendered_in_the_recipients_language(self):
        User.objects.filter(pk=self.user.pk).update(preferred_language="en")
        self.request_code()
        self.assertEqual(mail.outbox[0].subject, "Your OurTaiko sign-in code")

    @override_settings(EMAIL_CODE_LOGIN_ENABLED=False)
    def test_switch_hides_the_tab_and_the_routes(self):
        self.assertNotContains(self.client.get("/login/"), "邮箱验证码登录")
        self.assertEqual(self.client.get("/login/email/").status_code, 404)
        self.assertEqual(self.client.post("/login/email/code/", {"code": "000000"}).status_code, 404)
