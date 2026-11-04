"""Scoped reverse OIDC account mapping tests (no real token or network).

These tests validate only the RP business/identity boundary. Full OIDC claims,
PKCE, state, nonce and signature verification belong to Authlib end-to-end checks.
"""
from unittest import mock

from django.test import TestCase, override_settings
from oauth2_provider.models import Application

from accounts.models import ExternalLink, User
from accounts.sakura_oidc_identity import (
    ViewerBindingMissing, ViewerSubjectInvalid,
    bound_ourtaiko_user, verified_viewer_sub,
)


class ViewerOidcMappingTests(TestCase):
    def setUp(self):
        self.formal = Application.objects.create(
            name="Sakura",client_id="Sakura",client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="https://viewer.sakura-bot.cn/api/account/oidc/ourtaiko/callback",
        )
        self.staging = Application.objects.create(
            name="Sakura staging",client_id="Sakura-staging",client_type="confidential",
            authorization_grant_type="authorization-code",
            redirect_uris="https://localhost:8443/api/account/oidc/ourtaiko/callback",
        )
        self.user = User.objects.create_user(
            username="linked",nickname="Linked",email="linked@example.test",
            email_verified=True,password="Testing!4279",
        )
        self.sub = "9007199254740993"

    def test_canonical_large_decimal_and_invalid_inputs(self):
        self.assertEqual(verified_viewer_sub(self.sub),self.sub)
        for bad in ["0","01","1.0","1e2","-1","18446744073709551616",10,None]:
            with self.assertRaises(ViewerSubjectInvalid):
                verified_viewer_sub(bad)

    def test_only_formal_exact_pair_can_sign_in(self):
        ExternalLink.objects.create(application=self.staging,user=self.user,external_id=self.sub)
        with self.assertRaises(ViewerBindingMissing):
            bound_ourtaiko_user(self.sub)
        ExternalLink.objects.create(application=self.formal,user=self.user,external_id=self.sub)
        self.assertEqual(bound_ourtaiko_user(self.sub).pk,self.user.pk)
        with self.assertRaises(ViewerBindingMissing):
            bound_ourtaiko_user("9007199254740992")

    @override_settings(SAKURA_REVERSE_OIDC_ENABLED=False)
    def test_routes_are_closed_without_enablement(self):
        self.assertEqual(self.client.get("/login/sakura/").status_code,404)
        self.assertEqual(self.client.get("/login/sakura/callback/").status_code,404)

    @override_settings(SAKURA_REVERSE_OIDC_ENABLED=True)
    def test_callback_only_logs_into_existing_formal_pair(self):
        ExternalLink.objects.create(application=self.formal,user=self.user,external_id=self.sub)
        stub = mock.Mock()
        stub.authorize_access_token.return_value={"userinfo":{"sub":self.sub}}
        with mock.patch("accounts.sakura_oidc_views._client",return_value=stub):
            response=self.client.get("/login/sakura/callback/",{"code":"synthetic","state":"synthetic"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.session.get("_auth_user_id"),self.user.pk)
        self.assertEqual(User.objects.count(),1)
        self.assertEqual(ExternalLink.objects.count(),1)

    @override_settings(SAKURA_REVERSE_OIDC_ENABLED=True)
    def test_unlinked_callback_never_merges_email_or_creates_an_account(self):
        stub=mock.Mock()
        stub.authorize_access_token.return_value={"userinfo":{"sub":self.sub,"email":self.user.email}}
        with mock.patch("accounts.sakura_oidc_views._client",return_value=stub):
            response=self.client.get("/login/sakura/callback/",{"code":"synthetic","state":"synthetic"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(User.objects.count(),1)
        self.assertEqual(ExternalLink.objects.count(),0)
        self.assertNotIn("_auth_user_id",self.client.session)

    @override_settings(SAKURA_REVERSE_OIDC_ENABLED=True)
    def test_existing_ourtaiko_session_cannot_be_silently_switched(self):
        ExternalLink.objects.create(application=self.formal,user=self.user,external_id=self.sub)
        self.client.force_login(self.user)
        with mock.patch("accounts.sakura_oidc_views._client") as oidc:
            response=self.client.get("/login/sakura/callback/",{"code":"synthetic","state":"synthetic"})
        self.assertEqual(response.status_code,409)
        oidc.assert_not_called()
