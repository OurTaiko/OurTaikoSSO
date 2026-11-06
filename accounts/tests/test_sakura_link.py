"""Phase 2 identity/session policy; OIDC verification remains in Authlib."""
import time
from unittest import mock

from django.contrib.sessions.models import Session
from django.http import HttpResponseRedirect
from django.test import Client, TestCase, override_settings
from oauth2_provider.models import Application

from accounts.models import ExternalLink, User
from accounts.sakura_oidc_views import LINK_SESSION_KEY, RECOVERY_SESSION_KEY

STATE = "synthetic-state"


@override_settings(SAKURA_REVERSE_OIDC_ENABLED=True, SAKURA_REVERSE_LINK_ENABLED=True)
class SakuraLinkTests(TestCase):
    def setUp(self):
        self.app = Application.objects.create(
            name="Sakura", client_id="Sakura", client_type="confidential",
            authorization_grant_type="authorization-code",
        )
        self.user = User.objects.create_user(username="owner", nickname="Owner", password="OnlySynthetic123!")
        self.other = User.objects.create_user(username="other", nickname="Other", password="OnlySynthetic456!")
        self.sub = "9007199254740993"
        self.client.force_login(self.user)
        self.stub = mock.Mock()
        self.stub.authorize_redirect.return_value = HttpResponseRedirect(
            f"https://viewer.sakura-bot.cn/oidc/auth?state={STATE}"
        )
        self.stub.authorize_access_token.return_value = {
            "id_token": "library-verified-test-token",
            "userinfo": {"sub": self.sub, "iss": "https://viewer.sakura-bot.cn/oidc", "aud": "OurTaiko"},
        }
        self.stub.framework.get_state_data.return_value = {"data": "synthetic"}

    def start(self):
        with mock.patch("accounts.sakura_oidc_views._client", return_value=self.stub):
            return self.client.post("/link/sakura/")

    def finish(self, **params):
        with mock.patch("accounts.sakura_oidc_views._client", return_value=self.stub):
            return self.client.get("/login/sakura/callback/", {"state": STATE, "code": "synthetic", **params})

    def test_new_pair_and_repeat_preserve_session(self):
        key = self.client.session.session_key
        self.assertEqual(self.start().status_code, 302)
        self.assertRedirects(self.finish(), "/connections/", fetch_redirect_response=False)
        self.assertEqual(self.client.session.session_key, key)
        self.assertEqual(self.client.session["_auth_user_id"], self.user.pk)
        self.assertEqual(ExternalLink.objects.get().origin, "user_link")
        self.assertEqual(self.start().status_code, 302)
        self.assertEqual(self.finish().status_code, 302)
        self.assertEqual(ExternalLink.objects.count(), 1)
        self.assertEqual(User.objects.count(), 2)

    def test_same_pair_keeps_existing_origin(self):
        for origin in ["signup", "partner_link"]:
            ExternalLink.objects.all().delete()
            ExternalLink.objects.create(application=self.app, user=self.user, external_id=self.sub, origin=origin)
            self.start()
            self.assertEqual(self.finish().status_code, 302)
            self.assertEqual(ExternalLink.objects.get().origin, origin)

    def test_both_conflict_directions_never_overwrite(self):
        for user, sub in [(self.other, self.sub), (self.user, "999")]:
            ExternalLink.objects.all().delete()
            link = ExternalLink.objects.create(application=self.app, user=user, external_id=sub)
            self.start()
            self.assertEqual(self.finish().status_code, 409)
            link.refresh_from_db()
            self.assertEqual((link.user_id, link.external_id), (user.pk, sub))
            self.assertEqual(self.client.session["_auth_user_id"], self.user.pk)

    def test_claim_cannot_change_account(self):
        self.stub.authorize_access_token.return_value["userinfo"]["ourtaiko_sub"] = self.other.pk
        self.start()
        self.assertEqual(self.finish().status_code, 409)
        self.assertFalse(ExternalLink.objects.exists())

    def test_session_switch_logout_replay_expiry(self):
        self.start()
        self.client.force_login(self.other)
        self.assertEqual(self.finish().status_code, 409)

        self.client.logout()
        self.client.force_login(self.user)
        self.start()
        self.client.logout()
        self.assertNotEqual(self.finish().status_code, 302)
        self.assertFalse(ExternalLink.objects.exists())

        self.client.force_login(self.user)
        self.start()
        session = self.client.session
        intent = session[LINK_SESSION_KEY + ":" + STATE]
        intent["expires"] = int(time.time()) - 1
        session[LINK_SESSION_KEY + ":" + STATE] = intent
        session.save()
        self.assertEqual(self.finish().status_code, 409)

        self.start()
        self.assertEqual(self.finish().status_code, 302)
        self.assertEqual(self.finish().status_code, 409)  # the intent is consumed once

    def test_revoked_session_during_token_exchange(self):
        self.start()
        token = self.stub.authorize_access_token.return_value

        def revoke(*args, **kwargs):
            Session.objects.filter(session_key=self.client.session.session_key).delete()
            return token

        self.stub.authorize_access_token.side_effect = revoke
        self.assertIn(self.finish().status_code, [400, 409])
        self.assertFalse(ExternalLink.objects.exists())

    def test_cancel_returns_to_connections(self):
        self.start()
        response = self.finish(error="access_denied")
        self.assertRedirects(response, "/connections/", fetch_redirect_response=False)
        self.assertNotIn(LINK_SESSION_KEY + ":" + STATE, self.client.session)
        self.assertFalse(ExternalLink.objects.exists())

    def test_recovery_requires_both_expected_accounts(self):
        session = self.client.session
        session[RECOVERY_SESSION_KEY] = {"user": self.user.pk, "sub": "123", "expires": int(time.time()) + 600}
        session.save()
        self.start()
        self.assertEqual(self.finish().status_code, 409)
        self.stub.authorize_access_token.return_value["userinfo"]["sub"] = "123"
        self.start()
        self.assertEqual(self.finish().status_code, 302)
        self.assertNotIn(RECOVERY_SESSION_KEY, self.client.session)

    def test_recovery_hint_for_another_account_is_refused(self):
        session = self.client.session
        session[RECOVERY_SESSION_KEY] = {"user": self.other.pk, "sub": self.sub, "expires": int(time.time()) + 600}
        session.save()
        self.assertEqual(self.start().status_code, 409)
        self.stub.authorize_redirect.assert_not_called()

    @override_settings(SAKURA_REVERSE_LINK_ENABLED=False)
    def test_disabled(self):
        self.assertEqual(self.client.post("/link/sakura/").status_code, 404)
        self.assertNotContains(self.client.get("/connections/"), 'action="/link/sakura/"')

    def test_post_csrf_and_entry(self):
        self.assertEqual(self.client.get("/link/sakura/").status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post("/link/sakura/").status_code, 403)
        self.assertContains(self.client.get("/connections/"), 'action="/link/sakura/"')
