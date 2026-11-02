from datetime import timedelta
from io import StringIO
from threading import Barrier, Thread
from unittest import mock, skipUnless

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import IntegrityError, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from accounts.models import ApplicationAccess, ExternalAudit, ExternalLink, User

SAKURA_KEY = "s" * 64


@override_settings(SAKURA_CLIENT_ID="sakura-web")
class ExternalInterfaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="player",
            nickname="玩家",
            email="player@example.test",
            email_verified=True,
            password="Testing!4279",
        )
        self.sakura = self.application("sakura-web", SAKURA_KEY, allow_external_link=True)

    def application(self, client_id, key, **access):
        app = Application.objects.create(
            name=client_id,
            client_id=client_id,
            client_secret=key,
            client_type="confidential",
            authorization_grant_type="authorization-code",
            algorithm="RS256",
            redirect_uris="https://example.test/callback",
        )
        ApplicationAccess.objects.create(application=app, **access)
        return app

    def authorize(self, user=None, app=None, *, expires_in=3600):
        return AccessToken.objects.create(
            user=user or self.user,
            application=app or self.sakura,
            token=f"token-{AccessToken.objects.count()}",
            scope="openid profile",
            expires=timezone.now() + timedelta(seconds=expires_in),
        )

    def call(self, action, body, client_id="sakura-web", key=SAKURA_KEY, **headers):
        return self.client.post(
            "/internal/v1/external/" + action,
            body,
            content_type="application/json",
            HTTP_X_SERVICE_ID=client_id,
            HTTP_X_SERVICE_KEY=key,
            **headers,
        )

    def lookup(self, user_id=None, **kwargs):
        return self.call("lookup", {"id": user_id or self.user.pk}, **kwargs)

    def link(self, sakura_id="sakura_1", user_id=None):
        return self.call("link", {"id": user_id or self.user.pk, "externalId": sakura_id})

    def assertEnvelope(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"code", "message", "data"})
        self.assertEqual(body["code"], code)
        self.assertEqual(response["Cache-Control"], "no-store")
        if code != "OK":
            self.assertIsNone(body["data"])
        return body["data"]

    # Authentication and capability

    def test_every_rejection_uses_the_envelope(self):
        self.assertEnvelope(self.lookup(key="x" * 64), 401, "SERVICE_UNAUTHORIZED")
        self.assertEnvelope(self.lookup(client_id=""), 401, "SERVICE_UNAUTHORIZED")
        self.assertEnvelope(self.lookup(HTTP_ORIGIN="https://evil.test"), 403, "ORIGIN_INVALID")
        get = self.client.get(
            "/internal/v1/external/lookup", HTTP_X_SERVICE_ID="sakura-web", HTTP_X_SERVICE_KEY=SAKURA_KEY
        )
        self.assertEnvelope(get, 405, "METHOD_NOT_ALLOWED")
        self.assertEqual(get["Allow"], "POST")
        form = self.client.post(
            "/internal/v1/external/lookup",
            {"id": self.user.pk},
            HTTP_X_SERVICE_ID="sakura-web",
            HTTP_X_SERVICE_KEY=SAKURA_KEY,
        )
        self.assertEnvelope(form, 415, "CONTENT_TYPE_INVALID")
        self.assertEnvelope(self.call("lookup", {"id": "a" * 40000}), 400, "REQUEST_INVALID")
        self.assertEnvelope(self.call("lookup", [self.user.pk]), 400, "REQUEST_INVALID")

    def test_disabled_application_is_unauthorized(self):
        ApplicationAccess.objects.filter(application=self.sakura).update(enabled=False)
        self.assertEnvelope(self.lookup(), 401, "SERVICE_UNAUTHORIZED")

    def test_only_the_configured_sakura_application_with_the_flag_may_call(self):
        self.authorize()
        ApplicationAccess.objects.filter(application=self.sakura).update(allow_external_link=False)
        self.assertEnvelope(self.lookup(), 403, "SERVICE_FORBIDDEN")
        other = self.application("other-web", "o" * 64, allow_external_link=True, allow_profiles=True)
        self.authorize(app=other)
        self.assertEnvelope(self.lookup(client_id="other-web", key="o" * 64), 403, "SERVICE_FORBIDDEN")
        ApplicationAccess.objects.filter(application=self.sakura).update(allow_external_link=True)
        with override_settings(SAKURA_CLIENT_ID=""):
            self.assertEnvelope(self.lookup(), 403, "SERVICE_FORBIDDEN")
        self.assertEnvelope(self.lookup(), 200, "OK")

    def test_sakura_cannot_use_other_internal_interfaces(self):
        response = self.client.post(
            "/internal/v1/game/login",
            {"username": "player", "password": "Testing!4279"},
            content_type="application/json",
            HTTP_X_SERVICE_ID="sakura-web",
            HTTP_X_SERVICE_KEY=SAKURA_KEY,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "SERVICE_FORBIDDEN", "message": "此应用未获准使用该接口"})

    # Lookup

    def test_lookup_after_a_fresh_sign_in_returns_the_verified_email(self):
        self.authorize()
        data = self.assertEnvelope(self.lookup(), 200, "OK")
        self.assertEqual(
            data,
            {"id": self.user.pk, "nickname": "玩家", "email": "player@example.test", "emailVerified": True},
        )

    def test_lookup_of_a_bound_account_needs_no_token(self):
        ExternalLink.objects.create(user=self.user, sakura_id="sakura_1")
        self.assertEnvelope(self.lookup(), 200, "OK")

    def test_lookup_is_not_found_without_permission(self):
        self.assertEnvelope(self.lookup(), 404, "NOT_FOUND")
        self.authorize(expires_in=-1)
        self.assertEnvelope(self.lookup(), 404, "NOT_FOUND")
        other = self.application("other-web", "o" * 64)
        self.authorize(app=other)
        self.assertEnvelope(self.lookup(), 404, "NOT_FOUND")
        self.assertEnvelope(self.lookup("0" * 32), 404, "NOT_FOUND")

    def test_disabled_account_is_not_found_even_when_bound(self):
        ExternalLink.objects.create(user=self.user, sakura_id="sakura_1")
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEnvelope(self.lookup(), 404, "NOT_FOUND")

    def test_unverified_email_is_reported_only_to_an_authorized_caller(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=False)
        self.assertEnvelope(self.lookup(), 404, "NOT_FOUND")
        self.authorize()
        self.assertEnvelope(self.lookup(), 409, "EMAIL_UNVERIFIED")
        User.objects.filter(pk=self.user.pk).update(email_verified=True, email="")
        self.assertEnvelope(self.lookup(), 409, "EMAIL_UNVERIFIED")

    def test_lookup_rejects_malformed_bodies(self):
        for body in (
            {"id": self.user.pk.upper()},
            {"id": "abc"},
            {"id": 1},
            {"id": self.user.pk, "x": 1},
            {},
        ):
            self.assertEnvelope(self.call("lookup", body), 400, "REQUEST_INVALID")

    def test_lookup_is_rate_limited_per_account(self):
        self.authorize()
        for _ in range(60):
            self.assertEnvelope(self.lookup(), 200, "OK")
        response = self.lookup()
        self.assertEnvelope(response, 429, "RATE_LIMITED")
        self.assertEqual(response["Retry-After"], "600")
        self.assertEqual(ExternalAudit.objects.filter(result="RATE_LIMITED").count(), 1)

    # Link

    def test_link_after_a_fresh_sign_in(self):
        self.authorize()
        data = self.assertEnvelope(self.link(), 200, "OK")
        link = ExternalLink.objects.get()
        self.assertEqual((link.user_id, link.sakura_id), (self.user.pk, "sakura_1"))
        self.assertEqual(
            data, {"id": self.user.pk, "externalId": "sakura_1", "linkedAt": link.created_at.isoformat()}
        )

    def test_link_without_a_fresh_sign_in_is_not_found(self):
        self.assertEnvelope(self.link(), 404, "NOT_FOUND")
        self.authorize(expires_in=-1)
        self.assertEnvelope(self.link(), 404, "NOT_FOUND")
        self.assertEnvelope(self.link(user_id="0" * 32), 404, "NOT_FOUND")
        self.assertFalse(ExternalLink.objects.exists())

    def test_relinking_the_same_pair_is_idempotent_even_after_the_token_expired(self):
        token = self.authorize()
        first = self.assertEnvelope(self.link(), 200, "OK")
        token.delete()
        self.assertEqual(self.assertEnvelope(self.link(), 200, "OK"), first)
        self.assertEqual(ExternalLink.objects.count(), 1)

    def test_either_side_already_bound_is_rejected(self):
        self.authorize()
        self.assertEnvelope(self.link("sakura_1"), 200, "OK")
        self.assertEnvelope(self.link("sakura_2"), 409, "ALREADY_LINKED")
        other = User.objects.create_user(username="other", nickname="Other", password="Testing!4279")
        self.authorize(user=other)
        self.assertEnvelope(self.link("sakura_1", other.pk), 409, "ALREADY_LINKED")
        self.assertEqual(ExternalLink.objects.count(), 1)

    def test_bound_state_is_hidden_from_a_caller_without_a_fresh_sign_in(self):
        ExternalLink.objects.create(user=self.user, sakura_id="sakura_1")
        self.assertEnvelope(self.link("sakura_2"), 404, "NOT_FOUND")

    def test_concurrent_binding_of_the_same_sakura_id_loses_cleanly(self):
        self.authorize()
        with mock.patch.object(ExternalLink.objects, "create", side_effect=IntegrityError):
            self.assertEnvelope(self.link(), 409, "ALREADY_LINKED")
        self.assertFalse(ExternalLink.objects.exists())

    def test_link_rejects_malformed_bodies(self):
        self.authorize()
        for body in (
            {"id": self.user.pk},
            {"id": self.user.pk, "externalId": ""},
            {"id": self.user.pk, "externalId": "a" * 65},
            {"id": self.user.pk, "externalId": "has space"},
            {"id": self.user.pk, "externalId": 5},
            {"id": self.user.pk, "externalId": "ok", "extra": True},
        ):
            self.assertEnvelope(self.call("link", body), 400, "REQUEST_INVALID")

    def test_link_is_rate_limited_per_application(self):
        self.authorize()
        for _ in range(120):
            self.link()
        self.assertEnvelope(self.link(), 429, "RATE_LIMITED")

    # Audit and lifecycle

    def test_every_authenticated_call_is_audited_without_personal_data(self):
        self.authorize()
        self.lookup()
        self.link()
        self.call("lookup", {"id": "bad"})
        self.lookup(key="x" * 64)
        rows = list(
            ExternalAudit.objects.order_by("pk").values_list(
                "application", "action", "target_id", "external_id", "result"
            )
        )
        self.assertEqual(
            rows,
            [
                (self.sakura.pk, "lookup", self.user.pk, "", "OK"),
                (self.sakura.pk, "link", self.user.pk, "sakura_1", "OK"),
                (self.sakura.pk, "lookup", "", "", "REQUEST_INVALID"),
            ],
        )

    def test_cleanup_drops_audit_rows_past_retention(self):
        self.lookup()
        self.lookup()
        old = ExternalAudit.objects.first()
        ExternalAudit.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
        call_command("cleanup_auth", stdout=StringIO())
        self.assertEqual(
            list(ExternalAudit.objects.values_list("pk", flat=True)), [ExternalAudit.objects.last().pk]
        )
        self.assertNotIn(old.pk, ExternalAudit.objects.values_list("pk", flat=True))

    def test_revoking_the_connection_keeps_the_binding(self):
        self.authorize()
        self.assertEnvelope(self.link(), 200, "OK")
        self.client.force_login(self.user)
        page = self.client.get("/connections/")
        self.assertContains(page, "撤销授权不会解除账号绑定")
        self.client.post("/connections/revoke/", {"kind": "web", "key": self.sakura.pk})
        self.assertFalse(AccessToken.objects.filter(user=self.user).exists())
        self.client.logout()
        self.assertTrue(ExternalLink.objects.filter(user=self.user).exists())
        self.assertEnvelope(self.lookup(), 200, "OK")

    def test_deleting_the_account_removes_its_binding(self):
        ExternalLink.objects.create(user=self.user, sakura_id="sakura_1")
        self.user.delete()
        self.assertFalse(ExternalLink.objects.exists())


@skipUnless(connection.vendor == "postgresql", "needs real row locks")
@override_settings(SAKURA_CLIENT_ID="sakura-web")
class ExternalLinkRaceTests(TransactionTestCase):
    def test_two_accounts_racing_for_one_sakura_id(self):
        app = Application.objects.create(
            name="sakura-web",
            client_id="sakura-web",
            client_secret=SAKURA_KEY,
            client_type="confidential",
            authorization_grant_type="authorization-code",
            algorithm="RS256",
            redirect_uris="https://example.test/callback",
        )
        ApplicationAccess.objects.create(application=app, allow_external_link=True)
        users = [
            User.objects.create_user(username=f"racer{i}", nickname="Racer", password="Testing!4279")
            for i in range(2)
        ]
        for i, user in enumerate(users):
            AccessToken.objects.create(
                user=user,
                application=app,
                token=f"race-{i}",
                scope="openid profile",
                expires=timezone.now() + timedelta(hours=1),
            )
        barrier, codes = Barrier(2), []

        def bind(user):
            try:
                barrier.wait()
                response = Client().post(
                    "/internal/v1/external/link",
                    {"id": user.pk, "externalId": "contested"},
                    content_type="application/json",
                    HTTP_X_SERVICE_ID="sakura-web",
                    HTTP_X_SERVICE_KEY=SAKURA_KEY,
                )
                codes.append(response.json()["code"])
            finally:
                connections.close_all()

        threads = [Thread(target=bind, args=(user,)) for user in users]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(codes), ["ALREADY_LINKED", "OK"])
        self.assertEqual(ExternalLink.objects.filter(sakura_id="contested").count(), 1)


@override_settings(SAKURA_CLIENT_ID="sakura-web")
class ExternalAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", nickname="Root", email="root@example.test", password="Testing!4279"
        )
        self.user = User.objects.create_user(username="player", nickname="Player", password="Testing!4279")
        self.link = ExternalLink.objects.create(user=self.user, sakura_id="sakura_1")

    def test_only_a_superuser_can_delete_a_binding_and_nobody_edits_one(self):
        staff = User.objects.create_user(
            username="staff", nickname="Staff", password="Testing!4279", is_staff=True
        )
        staff.user_permissions.set(Permission.objects.filter(codename__endswith="externallink"))
        url = f"/admin/accounts/externallink/{self.user.pk}/delete/"
        self.client.force_login(staff)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.get("/admin/accounts/externallink/add/").status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/accounts/externallink/add/").status_code, 403)
        response = self.client.post(url, {"post": "yes"}, follow=True)
        self.assertContains(response, "请通知 Sakura 删除 Sakura ID sakura_1 的绑定")
        self.assertFalse(ExternalLink.objects.exists())

    def application_form(self, client_id, **overrides):
        data = {
            "name": client_id,
            "client_id": client_id,
            "enabled": "on",
            "client_type": "confidential",
            "authorization_grant_type": "authorization-code",
            "redirect_uris": "https://example.test/callback",
            "algorithm": "RS256",
            "allow_external_link": "on",
            **overrides,
        }
        return self.client.post("/admin/oauth2_provider/application/add/", data)

    def test_the_flag_is_accepted_only_for_the_configured_sakura_web_application(self):
        self.client.force_login(self.admin)
        error = "Sakura 外源接口只能开在"
        self.assertContains(self.application_form("other-web"), error)
        self.assertContains(self.application_form("sakura-web", client_type="public"), error)
        self.assertContains(
            self.application_form("sakura-web", authorization_grant_type="client-credentials"), error
        )
        with override_settings(SAKURA_CLIENT_ID=""):
            self.assertContains(self.application_form("sakura-web"), error)
        self.assertFalse(Application.objects.filter(client_id__in=["other-web", "sakura-web"]).exists())
        self.assertNotContains(self.application_form("sakura-web"), error)
        app = Application.objects.get(client_id="sakura-web")
        self.assertTrue(app.service_access.allow_external_link)
        self.assertFalse(app.service_access.allow_profiles)

    def test_consent_page_tells_the_user_about_email_and_binding(self):
        app = Application.objects.create(
            name="Sakura",
            client_id="sakura-web",
            client_secret="s" * 64,
            client_type="confidential",
            authorization_grant_type="authorization-code",
            algorithm="RS256",
            redirect_uris="https://example.test/callback",
        )
        access = ApplicationAccess.objects.create(application=app, allow_external_link=True)
        self.client.force_login(self.user)
        query = {
            "client_id": "sakura-web",
            "response_type": "code",
            "redirect_uri": "https://example.test/callback",
            "scope": "openid profile",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
        }
        self.assertContains(self.client.get("/o/authorize/", query), "读取你的邮箱")
        access.allow_external_link = False
        access.save()
        self.assertNotContains(self.client.get("/o/authorize/", query), "读取你的邮箱")
