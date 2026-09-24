"""Exercise local Docker stacks; keep a marked fixture to verify after migration.

Only ourtaiko-lab-* stacks are supported. Credentials stay in ignored .data.
"""

import argparse
import hashlib
import json
import os
import secrets
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests
from docker_lab import FAN, LAB, SSO, Stack


class HiddenFields(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.fields = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed", "check"])
    parser.add_argument("--name", default="first")
    args = parser.parse_args()
    stack = Stack(args.name)
    fixture_path = LAB / "migration-fixture.json"
    if args.action == "seed":
        if fixture_path.exists():
            raise RuntimeError("Fixture exists; use check instead")
        fixture = {"username": "docker" + secrets.token_hex(5), "password": secrets.token_urlsafe(24)}
        script = (
            "from accounts.models import User\n"
            f"u=User.objects.create_user(username={fixture['username']!r},password={fixture['password']!r},nickname='Docker迁移验证',email_verified=True)\n"
            "print(u.pk)\n"
        )
        fixture["user_id"] = (
            stack.compose("sso", "exec", "-T", "sso", "python", "manage.py", "shell", input=script.encode())
            .decode()
            .strip()
            .splitlines()[-1]
        )
        fixture_path.write_text(json.dumps(fixture))
    else:
        fixture = json.loads(fixture_path.read_text())
    browser = requests.Session()
    browser.trust_env = False
    origin = "http://127.0.0.1:18080"
    api = origin + "/api/v1"
    sso = "http://127.0.0.1:18090"

    def post_form(response, data, **kwargs):
        return browser.post(
            response.url, data={**HiddenFields(response.text).fields, **data}, timeout=20, **kwargs
        )

    response = browser.get(api + "/auth/sso/login?returnTo=/me/profile", timeout=20)
    assert urlsplit(response.url).path == "/login/", "SSO login page unavailable"
    response = post_form(response, {"username": fixture["username"], "password": fixture["password"]})
    if urlsplit(response.url).path == "/o/authorize/":
        response = post_form(response, {"allow": "true"})
    assert response.url == origin + "/me/profile", "OIDC callback failed"
    session = browser.get(api + "/me", timeout=10).json()
    assert session["user"]["id"] == fixture["user_id"], "Account ID changed"
    print("PASS web OIDC + PKCE login and stable user ID", flush=True)
    if args.action == "seed":
        body = "TITLE:Docker migration fixture\nBPM:120\nWAVE:cbr.mp3\nCOURSE:Oni\nLEVEL:5\n#START\n1000,\n#END\n"
        audio = (FAN / "internal/audio/testdata/cbr.mp3").read_bytes()
        cover = (SSO / "static/icon.png").read_bytes()
        response = browser.post(
            api + "/charts",
            headers={
                "Origin": origin,
                "X-CSRF-Token": session["csrfToken"],
                "Idempotency-Key": secrets.token_hex(16),
            },
            files={
                "tja": ("smoke.tja", body.encode(), "application/octet-stream"),
                "audio": ("cbr.mp3", audio, "audio/mpeg"),
                "cover": ("cover.png", cover, "image/png"),
            },
            timeout=30,
        )
        assert response.status_code == 201, f"Upload failed: HTTP {response.status_code}"
        chart = response.json()
        fixture.update(chart_id=chart["id"], version_id=chart["versionId"], audio_sha=digest(audio))
        fixture_path.write_text(json.dumps(fixture))
    chart = browser.get(api + "/charts/" + fixture["chart_id"], timeout=10).json()
    assert chart["ownerId"] == fixture["user_id"] and chart["uploader"] == "Docker迁移验证"
    for kind in ["audio", "tja"]:
        response = browser.get(
            api + f"/charts/{fixture['chart_id']}/versions/{fixture['version_id']}/{kind}", timeout=20
        )
        assert response.status_code == 200
        if kind == "audio":
            assert digest(response.content) == fixture["audio_sha"]
        elif args.action == "seed":
            fixture["tja_sha"] = digest(response.content)
        else:
            assert digest(response.content) == fixture["tja_sha"]
    response = browser.get(api + "/charts/" + fixture["chart_id"] + "/cover", timeout=10)
    assert response.status_code == 200 and response.headers["Content-Type"] == "image/webp"
    if args.action == "seed":
        fixture["cover_sha"] = digest(response.content)
    else:
        assert digest(response.content) == fixture["cover_sha"]
    print("PASS audio/TJA download hashes and database WebP cover", flush=True)
    response = browser.post(
        api + "/game/login",
        json={"username": fixture["username"], "password": fixture["password"]},
        timeout=10,
    )
    assert response.status_code == 200
    native = response.json()
    assert native["user"]["id"] == fixture["user_id"]
    auth = {"Authorization": "Bearer " + native["accessToken"], "Idempotency-Key": secrets.token_hex(16)}
    if args.action == "seed":
        score = dict(
            songId=fixture["chart_id"],
            versionId=fixture["version_id"],
            difficulty="Oni",
            good=1,
            ok=0,
            bad=0,
            score=1000,
            drumroll=0,
            max_combo=1,
        )
        response = browser.post(api + "/game/scores", json=score, headers=auth, timeout=10)
        assert response.status_code == 201
        assert browser.post(api + "/game/scores", json=score, headers=auth, timeout=10).status_code == 200
    bootstrap = browser.get(api + "/game/bootstrap", headers=auth, timeout=10).json()
    assert any(
        score["songId"] == fixture["chart_id"] and score["score"] == 1000 for score in bootstrap["scores"]
    )
    print("PASS game authentication, score persistence and restored chart ownership", flush=True)
    response = browser.post(
        api + "/auth/logout",
        headers={"Origin": origin, "X-CSRF-Token": session["csrfToken"]},
        json={},
        timeout=10,
    )
    assert response.status_code == 200
    assert browser.get(api + "/me", timeout=10).json()["user"] is None
    assert browser.get(sso + "/readyz", timeout=10).status_code == 200
    if args.action == "seed":
        fixture_path.write_text(json.dumps(fixture))
    print("PASS logout; marked migration fixture retained only in this local Docker lab", flush=True)


if __name__ == "__main__":
    main()
