"""HTTP smoke test for ONLY the synthetic localhost unification Docker stack."""

from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests
from unification_lab import AFTER, shell

ORIGIN = "http://127.0.0.1:28080"
SSO = "http://127.0.0.1:28090"
API = ORIGIN + "/api/v1"


class HiddenFields(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.fields = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden" and values.get("name"):
            self.fields[values["name"]] = values.get("value", "")


def main():
    browser = requests.Session()
    browser.trust_env = False
    response = browser.get(API + "/auth/sso/login?returnTo=/", timeout=15)
    assert urlsplit(response.url).netloc == "127.0.0.1:28090"
    assert urlsplit(response.url).path == "/login/"
    response = browser.post(
        response.url,
        data={
            **HiddenFields(response.text).fields,
            "username": "labadmin",
            "password": "LocalOnly-Unification-4279",
        },
        timeout=15,
    )
    if urlsplit(response.url).path == "/o/authorize/":
        response = browser.post(
            response.url, data={**HiddenFields(response.text).fields, "allow": "true"}, timeout=15
        )
    assert response.url == ORIGIN + "/", "OIDC callback failed"
    response = browser.get(API + "/me", timeout=10)
    assert response.status_code == 200
    me = response.json()
    assert me["user"]["isAdmin"]
    print("PASS: real Fanmade -> SSO OIDC/PKCE -> callback -> authenticated admin", flush=True)
    game = browser.post(
        API + "/game/login",
        json={"username": "labadmin", "password": "LocalOnly-Unification-4279"},
        timeout=15,
    )
    assert game.status_code == 200
    native = game.json()
    assert native["user"]["id"] == me["user"]["id"] and native["user"]["isAdmin"]
    assert (
        browser.get(
            API + "/game/bootstrap", headers={"Authorization": "Bearer " + native["accessToken"]}, timeout=10
        ).status_code
        == 200
    )
    try:
        shell(
            AFTER,
            "from accounts.models import ClientRole\nClientRole.objects.filter(client__client_id='fanmade-web').update(is_admin=False)",
        )
        assert not browser.get(API + "/me", timeout=10).json()["user"]["isAdmin"]
    finally:
        shell(
            AFTER,
            "from accounts.models import ClientRole\nClientRole.objects.filter(client__client_id='fanmade-web').update(is_admin=True)",
        )
    print("PASS: live role removal reaches the existing Fanmade session", flush=True)
    # Both kinds of existing sessions must survive turning off legacy authentication.
    try:
        shell(
            AFTER,
            "from accounts.models import ApplicationAccess\nApplicationAccess.objects.filter(application__client_id='fanmade-web').update(accept_legacy=False)",
        )
        rejected = browser.post(
            SSO + "/internal/v1/users/lookup",
            json={"userIds": []},
            headers={"X-Service-ID": "fanmade", "X-Service-Key": "a" * 64},
            timeout=10,
        )
        assert rejected.status_code == 401
        assert browser.get(API + "/me", timeout=10).json()["user"]["isAdmin"]
        assert (
            browser.get(
                API + "/game/bootstrap",
                headers={"Authorization": "Bearer " + native["accessToken"]},
                timeout=10,
            ).status_code
            == 200
        )
        print("PASS: one non-hex Application credential serves web + game; old key disabled", flush=True)
    finally:
        shell(
            AFTER,
            "from accounts.models import ApplicationAccess\nApplicationAccess.objects.filter(application__client_id='fanmade-web').update(accept_legacy=True)",
        )
        browser.post(
            SSO + "/internal/v1/game/revoke",
            json={"accessToken": native["accessToken"]},
            headers={"X-Service-ID": "fanmade-web", "X-Service-Key": "Opaque-Application_Secret" * 3},
            timeout=10,
        )
    print("PASS: live compatibility switch restored for local review", flush=True)


if __name__ == "__main__":
    main()
