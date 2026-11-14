"""First sign-in through Sakura Viewer creates an ordinary account (account policy §3.1–3.2).

The OIDC client is mocked as in test_sakura_oidc_reverse; what is tested is the account policy
applied to claims that Authlib has already verified.
"""
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from oauth2_provider.models import Application

from accounts.models import ExternalLink, User

SUB = "9007199254740993"


def claims(**extra):
    return {
        "sub": SUB,
        "iss": "https://viewer.sakura-bot.cn/oidc",
        "aud": "OurTaiko",
        "email": "Viewer.Player@Example.test",
        "email_verified": True,
        "name": "Viewer 玩家",
        **extra,
    }


@override_settings(SAKURA_REVERSE_OIDC_ENABLED=True, SAKURA_REVERSE_SIGNUP_ENABLED=True)
class SakuraSignupTests(TestCase):
    def setUp(self):
        self.formal = Application.objects.create(
            name="Sakura",
            client_id="Sakura",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="https://viewer.sakura-bot.cn/api/account/oidc/ourtaiko/callback",
        )

    def callback(self, verified):
        stub = mock.Mock()
        stub.authorize_access_token.return_value = {"id_token": "verified-by-authlib", "userinfo": verified}
        with mock.patch("accounts.sakura_oidc_views._client", return_value=stub):
            return self.client.get("/login/sakura/callback/", {"code": "c", "state": "s"})

    def sign_up(self, username="viewerplayer", nickname="Viewer 玩家"):
        return self.client.post("/login/sakura/signup/", {"username": username, "nickname": nickname})

    def test_first_sign_in_creates_an_ordinary_account_and_the_second_returns_to_it(self):
        response = self.callback(claims())
        self.assertRedirects(response, "/login/sakura/signup/", fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 0)
        page = self.client.get("/login/sakura/signup/")
        self.assertContains(page, "viewer.player@example.test")
        # Under no-referrer the browser would post the form with "Origin: null" and fail CSRF.
        self.assertNotEqual(page["Referrer-Policy"], "no-referrer")
        self.assertContains(page, 'value="Viewer 玩家"')

        self.assertRedirects(self.sign_up(), "/", fetch_redirect_response=False)
        user = User.objects.get()
        self.assertEqual(self.client.session["_auth_user_id"], user.pk)
        self.assertEqual((user.username, user.nickname), ("viewerplayer", "Viewer 玩家"))
        self.assertEqual(user.email, "viewer.player@example.test")
        self.assertTrue(user.email_verified and user.is_active)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(user.has_usable_password())
        link = ExternalLink.objects.get()
        self.assertEqual((link.application, link.user, link.external_id), (self.formal, user, SUB))
        self.assertEqual(link.origin, ExternalLink.Origin.SIGNUP)

        self.client.logout()
        self.assertRedirects(self.callback(claims()), "/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], user.pk)
        self.assertEqual((User.objects.count(), ExternalLink.objects.count()), (1, 1))

    def test_unverified_or_missing_email_creates_nothing(self):
        for verified in (claims(email_verified=False), claims(email_verified="true"), claims(email="")):
            self.assertEqual(self.callback(verified).status_code, 409)
        bad = claims()
        del bad["email"]
        self.assertEqual(self.callback(bad).status_code, 409)
        self.assertEqual(self.client.get("/login/sakura/signup/").status_code, 400)
        self.assertEqual(User.objects.count(), 0)

    def test_an_existing_email_is_never_merged(self):
        owner = User.objects.create_user(
            username="owner", nickname="Owner", email="viewer.player@example.test", password="Testing!4279"
        )
        response = self.callback(claims())
        self.assertContains(response, "请先登录原账号", status_code=409)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(ExternalLink.objects.exists())
        self.assertTrue(User.objects.get(pk=owner.pk).has_usable_password())

    def test_a_viewer_side_pair_blocks_sign_up_and_grants_nothing(self):
        owner = User.objects.create_user(username="owner", nickname="Owner", email="o@example.test")
        response = self.callback(claims(ourtaiko_sub=owner.pk))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(ExternalLink.objects.exists())

    def test_a_disabled_paired_account_is_not_replaced(self):
        owner = User.objects.create_user(username="owner", nickname="Owner", email="o@example.test", is_active=False)
        ExternalLink.objects.create(application=self.formal, user=owner, external_id=SUB)
        self.assertEqual(self.callback(claims()).status_code, 403)
        self.assertEqual(User.objects.count(), 1)

    def test_staging_pairs_do_not_count_and_do_not_block(self):
        staging = Application.objects.create(
            name="Sakura staging",
            client_id="Sakura-staging",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="https://localhost:8443/api/account/oidc/ourtaiko/callback",
        )
        other = User.objects.create_user(username="staged", nickname="Staged", email="s@example.test")
        ExternalLink.objects.create(application=staging, user=other, external_id=SUB)
        self.callback(claims())
        self.sign_up()
        self.assertNotEqual(self.client.session["_auth_user_id"], other.pk)
        self.assertEqual(ExternalLink.objects.filter(application=self.formal).count(), 1)

    def test_changes_while_the_page_was_open_are_rechecked(self):
        self.callback(claims())
        User.objects.create_user(username="early", nickname="Early", email="viewer.player@example.test")
        self.assertEqual(self.sign_up().status_code, 409)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.client.get("/login/sakura/signup/").status_code, 400)

    def test_a_taken_username_keeps_the_page(self):
        User.objects.create_user(username="viewerplayer", nickname="Taken", email="t@example.test")
        self.callback(claims())
        response = self.sign_up()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        # Nor a name that differs from someone's only in case.
        response = self.sign_up(username="ViewerPlayer")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        self.assertRedirects(self.sign_up(username="viewer2"), "/", fetch_redirect_response=False)

    def test_the_pending_sign_up_expires(self):
        self.callback(claims())
        with mock.patch("accounts.sakura_oidc_views.time.time", return_value=10**10):
            self.assertEqual(self.sign_up().status_code, 400)
        self.assertEqual(User.objects.count(), 0)

    def test_an_unusable_nickname_is_left_for_the_person(self):
        self.callback(claims(name="a\nb"))
        self.assertContains(self.client.get("/login/sakura/signup/"), 'name="nickname"')
        self.assertNotContains(self.client.get("/login/sakura/signup/"), 'value="a')

    def test_the_account_can_set_a_password_by_mail(self):
        self.callback(claims())
        self.sign_up()
        self.client.logout()
        with mock.patch("accounts.views.allow_attempt", return_value=True):
            self.client.post("/password/reset/", {"email": "viewer.player@example.test"})
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(SAKURA_REVERSE_SIGNUP_ENABLED=False)
    def test_switched_off_it_keeps_the_linked_only_behaviour(self):
        self.assertEqual(self.callback(claims()).status_code, 409)
        self.assertEqual(self.client.get("/login/sakura/signup/").status_code, 404)
        self.assertEqual(User.objects.count(), 0)
