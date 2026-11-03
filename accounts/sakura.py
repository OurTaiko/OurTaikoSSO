"""Server-only client for Viewer -> OurTaiko reverse account linking.

This module does not define pages or enable the feature. The caller must explicitly
verify the end user's intent. Never log Viewer credentials or upstream response bodies.
The Viewer service credential is separately issued by Viewer: it is NOT the existing
Sakura OIDC Client Secret or OurTaiko's SSO_SERVICE_KEY.
"""
import ipaddress
import json
import re
from datetime import datetime

import requests
from django.conf import settings

VIEWER_ORIGIN = "https://viewer.sakura-bot.cn"
VIEWER_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
OURTAIKO_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_JSON_BYTES = 32 * 1024


class SakuraServiceError(Exception):
    """A safe, structured result. Never includes an HTTP response or credentials."""

    def __init__(self, status=503, code="UNAVAILABLE", retry_after=None):
        self.status = status
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


class SakuraUnknownResult(SakuraServiceError):
    """Remote commit may have happened; retry only the identical account pair."""


def _viewer_id(value):
    return isinstance(value, str) and VIEWER_ID.fullmatch(value) and int(value) <= 2**64 - 1


def _ourtaiko_id(value):
    return isinstance(value, str) and OURTAIKO_ID.fullmatch(value)


def _configured():
    if not getattr(settings, "SAKURA_REVERSE_ENABLED", False):
        raise SakuraServiceError()
    sid = getattr(settings, "SAKURA_REVERSE_SERVICE_ID", "")
    key = getattr(settings, "SAKURA_REVERSE_SERVICE_KEY", "")
    if not sid or not isinstance(key, str) or len(key) < 24:
        raise SakuraServiceError()
    return sid, key


def _valid_linked_at(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _validated_success(action, data, request_body):
    if not isinstance(data, dict):
        raise SakuraUnknownResult()
    if action == "login":
        if set(data) != {"id"} or not _viewer_id(data.get("id")):
            raise SakuraUnknownResult()
    elif action == "lookup":
        if set(data) != {"id", "nickname", "email", "emailVerified", "linkedId"}:
            raise SakuraUnknownResult()
        if (data["id"] != request_body["id"]
            or not isinstance(data["nickname"], str)
            or not isinstance(data["email"], str)
            or data["emailVerified"] is not True
            or (data["linkedId"] is not None and not _ourtaiko_id(data["linkedId"]))):
            raise SakuraUnknownResult()
    elif action in {"link", "unlink"}:
        keys = {"id", "externalId", "linkedAt"} if action == "link" else {"id", "externalId"}
        if (set(data) != keys or data["id"] != request_body["id"]
            or data["externalId"] != request_body["externalId"]
            or (action == "link" and not _valid_linked_at(data["linkedAt"]))):
            raise SakuraUnknownResult()
    elif action == "links":
        if set(data) != {"links", "next"} or not isinstance(data["links"], list) or len(data["links"]) > 1000:
            raise SakuraUnknownResult()
        if data["next"] != "" and not _viewer_id(data["next"]):
            raise SakuraUnknownResult()
        for link in data["links"]:
            if (not isinstance(link, dict) or set(link) != {"id", "externalId", "linkedAt"}
                or not _viewer_id(link["id"]) or not _ourtaiko_id(link["externalId"])
                or not _valid_linked_at(link["linkedAt"])):
                raise SakuraUnknownResult()
    return data


def _post(action, payload, *, end_user_ip=None):
    sid, key = _configured()
    if action not in {"login", "lookup", "link", "unlink", "links"}:
        raise ValueError("Unexpected Sakura action")
    if end_user_ip is not None:
        try:
            end_user_ip = str(ipaddress.ip_address(end_user_ip))
        except ValueError:
            raise SakuraServiceError(400, "REQUEST_INVALID") from None

    headers = {
        "X-Service-ID": sid,
        "X-Service-Key": key,
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    if end_user_ip is not None:
        headers["X-End-User-IP"] = end_user_ip
    # Never use user-controlled endpoint URL or an ambient HTTP_PROXY.
    url = VIEWER_ORIGIN + "/internal/v1/external/" + action
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise SakuraServiceError(400, "REQUEST_INVALID")
    session = requests.Session()
    session.trust_env = False
    try:
        with session.post(
            url, headers=headers, data=raw, timeout=(3, 8),
            allow_redirects=False, stream=True,
        ) as response:
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json" or response.is_redirect:
                raise SakuraUnknownResult()
            chunks = []
            count = 0
            for part in response.iter_content(chunk_size=4096):
                count += len(part)
                if count > MAX_JSON_BYTES:
                    raise SakuraUnknownResult()
                chunks.append(part)
            try:
                result = json.loads(b"".join(chunks).decode("utf-8", errors="strict"))
            except (UnicodeError, ValueError):
                raise SakuraUnknownResult() from None
            if not isinstance(result, dict) or set(result) != {"code", "message", "data"}:
                raise SakuraUnknownResult()
            code, message, data = result["code"], result["message"], result["data"]
            if not isinstance(code, str) or not isinstance(message, str):
                raise SakuraUnknownResult()
            if response.status_code == 200:
                if code != "OK" or message != "":
                    raise SakuraUnknownResult()
                return _validated_success(action, data, payload)
            expected = {
                "REQUEST_INVALID": 400, "CONTENT_TYPE_INVALID": 415,
                "SERVICE_UNAUTHORIZED": 401, "SERVICE_FORBIDDEN": 403,
                "LOGIN_INVALID": 401, "NOT_FOUND": 404, "EMAIL_UNVERIFIED": 409,
                "ALREADY_LINKED": 409, "RATE_LIMITED": 429,
                "METHOD_NOT_ALLOWED": 405, "ORIGIN_INVALID": 403, "UNAVAILABLE": 503,
            }
            if data is not None or expected.get(code) != response.status_code:
                raise SakuraUnknownResult()
            if code == "RATE_LIMITED":
                raw_retry = response.headers.get("Retry-After", "")
                if not re.fullmatch(r"[0-9]{1,8}", raw_retry):
                    raise SakuraUnknownResult()
                seconds = min(3600, max(1, int(raw_retry)))
                raise SakuraServiceError(429, code, seconds)
            if response.status_code >= 500 or code == "UNAVAILABLE":
                raise SakuraUnknownResult()
            raise SakuraServiceError(response.status_code, code)
    except requests.RequestException:
        # An interrupted link may have committed on Viewer. Caller must not
        # infer rollback or change either ID. Do not include exception text.
        raise SakuraUnknownResult() from None
    finally:
        session.close()


def login_viewer(login, password, end_user_ip):
    """Returns only Viewer ID; never retries or persists the supplied password."""
    if not isinstance(login, str) or not isinstance(password, str):
        raise SakuraServiceError(400, "REQUEST_INVALID")
    if not login.strip() or len(login) > 255 or not password or len(password) > 1024:
        raise SakuraServiceError(400, "REQUEST_INVALID")
    return _post("login", {"login": login, "password": password}, end_user_ip=end_user_ip)


def lookup_viewer(viewer_id):
    if not _viewer_id(viewer_id):
        raise SakuraServiceError(400, "REQUEST_INVALID")
    return _post("lookup", {"id": viewer_id})


def link_viewer(viewer_id, ourtaiko_id):
    if not _viewer_id(viewer_id) or not _ourtaiko_id(ourtaiko_id):
        raise SakuraServiceError(400, "REQUEST_INVALID")
    return _post("link", {"id": viewer_id, "externalId": ourtaiko_id})


def unlink_viewer(viewer_id, ourtaiko_id):
    if not _viewer_id(viewer_id) or not _ourtaiko_id(ourtaiko_id):
        raise SakuraServiceError(400, "REQUEST_INVALID")
    return _post("unlink", {"id": viewer_id, "externalId": ourtaiko_id})


def list_viewer_links(after=""):
    if after and not _viewer_id(after):
        raise SakuraServiceError(400, "REQUEST_INVALID")
    return _post("links", {"after": after})
