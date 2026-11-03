"""Focused reverse-client contract tests: no live HTTP or passwords in output."""
import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from accounts import sakura


class FakeResponse:
    def __init__(self, data, status=200, retry_after=None):
        self.status_code = status
        self.headers = {"Content-Type": "application/json; charset=utf-8"}
        if retry_after is not None:
            self.headers["Retry-After"] = retry_after
        self.is_redirect = False
        self.body = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, chunk_size=4096):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i + chunk_size]


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.trust_env = True
        self.kwargs = None
        self.url = None

    def post(self, url, **kwargs):
        self.url, self.kwargs = url, kwargs
        return self.response

    def close(self):
        pass


@override_settings(
    SAKURA_REVERSE_ENABLED=True,
    SAKURA_REVERSE_SERVICE_ID="OurTaiko",
    SAKURA_REVERSE_SERVICE_KEY="test-service-key-do-not-use-in-production-012345",
)
class SakuraClientTests(SimpleTestCase):
    viewer_id = "1996075848453"
    sub = "3c4359f94671abb5abba6135735168ed"

    def call(self, name, request, expected, **kwargs):
        session = FakeSession(FakeResponse(expected))
        with mock.patch.object(sakura.requests, "Session", return_value=session):
            result = getattr(sakura, name)(*request, **kwargs)
        self.assertEqual(session.url, sakura.VIEWER_ORIGIN + "/internal/v1/external/" + name.replace("_viewer", "").replace("list_", ""))
        self.assertFalse(session.trust_env)
        self.assertFalse(session.kwargs["allow_redirects"])
        self.assertNotIn("Cookie", session.kwargs["headers"])
        self.assertNotIn("Authorization", session.kwargs["headers"])
        return result

    def test_login_returns_only_verified_viewer_id_and_no_browser_token(self):
        payload = {"code": "OK", "message": "", "data": {"id": self.viewer_id}}
        session = FakeSession(FakeResponse(payload))
        with mock.patch.object(sakura.requests, "Session", return_value=session):
            data = sakura.login_viewer("player", "mock-password", "127.0.0.1")
        self.assertEqual(data, {"id": self.viewer_id})
        self.assertEqual(session.kwargs["headers"]["X-End-User-IP"], "127.0.0.1")
        self.assertNotIn("mock-password", repr(data))
        self.assertNotIn("Cookie", session.kwargs["headers"])
        self.assertFalse(session.kwargs["allow_redirects"])

    def test_link_matches_exact_pair_and_offset_timestamp(self):
        data = {"id": self.viewer_id, "externalId": self.sub, "linkedAt": "2026-11-03T07:00:36.936729+00:00"}
        session = FakeSession(FakeResponse({"code": "OK", "message": "", "data": data}))
        with mock.patch.object(sakura.requests, "Session", return_value=session):
            result = sakura.link_viewer(self.viewer_id, self.sub)
        self.assertEqual(result, data)

    def test_wrong_pair_is_unknown_not_success(self):
        data = {"id": "2", "externalId": self.sub, "linkedAt": "2026-11-03T07:00:36Z"}
        session = FakeSession(FakeResponse({"code": "OK", "message": "", "data": data}))
        with mock.patch.object(sakura.requests, "Session", return_value=session):
            with self.assertRaises(sakura.SakuraUnknownResult):
                sakura.link_viewer(self.viewer_id, self.sub)

    def test_409_is_a_structured_conflict_not_a_retry(self):
        session = FakeSession(FakeResponse({"code": "ALREADY_LINKED", "message": "", "data": None}, status=409))
        with mock.patch.object(sakura.requests, "Session", return_value=session):
            with self.assertRaises(sakura.SakuraServiceError) as raised:
                sakura.link_viewer(self.viewer_id, self.sub)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "ALREADY_LINKED"))

    def test_invalid_unlink_pair_never_sends_http(self):
        with mock.patch.object(sakura.requests, "Session") as session:
            with self.assertRaises(sakura.SakuraServiceError):
                sakura.unlink_viewer("not-an-id", self.sub)
            session.assert_not_called()

    def test_disabled_feature_never_sends_http(self):
        with override_settings(SAKURA_REVERSE_ENABLED=False), mock.patch.object(sakura.requests, "Session") as session:
            with self.assertRaises(sakura.SakuraServiceError):
                sakura.lookup_viewer(self.viewer_id)
            session.assert_not_called()
