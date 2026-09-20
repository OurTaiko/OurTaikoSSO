"""Verify the running local Fanmade/SSO boundary with disposable records."""

import secrets
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from dotenv import dotenv_values

from accounts.models import BrowserSession, User


class HiddenFields(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")


class Command(BaseCommand):
    help = "Verify local SSO login, Fanmade uploads/game scores, nickname sync and password revocation."

    def handle(self, *args, **options):
        root = settings.BASE_DIR.parent
        backend = root / "Fanmade/backend"
        config = dotenv_values(backend / ".env")
        origin, issuer = config["APP_ORIGIN"], config["SSO_ISSUER"]
        if not settings.DEBUG or any(
            urlsplit(u).hostname not in {"localhost", "127.0.0.1"} for u in [origin, issuer]
        ):
            raise CommandError("Local development servers only")
        if urlsplit(config["DATABASE_URL"]).path != "/ourtaiko_fanmade_sso_dev":
            raise CommandError("Use the separate ourtaiko_fanmade_sso_dev database")
        password = secrets.token_urlsafe(24)
        user = User.objects.create_user(
            username="flow" + secrets.token_hex(6),
            nickname="联调测试",
            password=password,
            email_verified=True,
        )
        chart_id = None
        browser = requests.Session()
        browser.trust_env = False
        api = origin + "/api/v1"

        def post_form(response, data, **kwargs):
            return browser.post(
                response.url, data={**HiddenFields(response.text).fields, **data}, timeout=20, **kwargs
            )

        def login():
            response = browser.get(api + "/auth/sso/login?returnTo=/me/profile", timeout=20)
            if urlsplit(response.url).path == "/login/":
                response = post_form(response, {"username": user.username, "password": password})
            assert urlsplit(response.url).path == "/o/authorize/", "SSO consent page missing"
            response = post_form(response, {"allow": "true"}, allow_redirects=False)
            assert response.status_code == 302, "Authorization did not redirect"
            callback = response.headers["Location"]
            response = browser.get(callback, timeout=20)
            assert response.url == origin + "/me/profile", "OIDC callback failed"
            session = browser.get(api + "/me", timeout=10).json()
            assert session["user"]["id"] == user.pk
            replay = browser.get(callback, timeout=10, allow_redirects=False)
            assert replay.status_code == 303 and replay.headers["Location"].endswith("/login?error=sso")
            return session

        try:
            session = login()
            self.stdout.write(
                "PASS OIDC + PKCE login, stable subject, return path and callback replay rejection"
            )
            headers = {
                "Origin": origin,
                "X-CSRF-Token": session["csrfToken"],
                "Idempotency-Key": secrets.token_hex(16),
            }
            body = (
                "TITLE:SSO boundary smoke\nBPM:120\nWAVE:cbr.mp3\nCOURSE:Oni\nLEVEL:5\n#START\n1000,\n#END\n"
            )
            response = browser.post(
                api + "/charts",
                headers=headers,
                files={
                    "tja": ("smoke.tja", body.encode(), "application/octet-stream"),
                    "audio": (
                        "cbr.mp3",
                        (backend / "internal/audio/testdata/cbr.mp3").read_bytes(),
                        "audio/mpeg",
                    ),
                },
                timeout=30,
            )
            assert response.status_code == 201, (
                "Authenticated upload failed",
                response.status_code,
                response.text,
            )
            chart = response.json()
            chart_id = chart["id"]
            assert chart["ownerId"] == user.pk and chart["uploader"] == user.nickname
            game = requests.Session()
            game.trust_env = False
            response = game.post(
                api + "/game/login", json={"username": user.username, "password": password}, timeout=10
            )
            assert response.status_code == 200 and not response.cookies, "Game login contract failed"
            native = response.json()
            assert native["user"]["id"] == user.pk and native["expiresIn"] == 604800
            auth = {
                "Authorization": "Bearer " + native["accessToken"],
                "Idempotency-Key": secrets.token_hex(16),
            }
            score = dict(
                songId=chart_id,
                versionId=chart["versionId"],
                difficulty="Oni",
                good=1,
                ok=0,
                bad=0,
                score=1000,
                drumroll=0,
                max_combo=1,
            )
            response = game.post(api + "/game/scores", json=score, headers=auth, timeout=10)
            assert response.status_code == 201 and response.json()["userId"] == user.pk
            assert game.post(api + "/game/scores", json=score, headers=auth, timeout=10).status_code == 200
            bootstrap = game.get(api + "/game/bootstrap", headers=auth, timeout=10).json()
            assert any(s["songId"] == chart_id for s in bootstrap["scores"])
            assert browser.get(api + "/game/bootstrap", timeout=10).json()["user"] is None
            self.stdout.write(
                "PASS upload ownership, original game API login, score persistence/idempotency and cookie isolation"
            )
            profile = browser.get(issuer + "/profile/", timeout=10)
            assert post_form(profile, {"nickname": "同步后的昵称"}).status_code == 200
            assert browser.get(api + "/me", timeout=10).json()["user"]["nickname"] == "同步后的昵称"
            assert (
                game.get(api + "/game/bootstrap", headers=auth, timeout=10).json()["user"]["nickname"]
                == "同步后的昵称"
            )
            public = browser.get(api + "/charts/" + chart_id, timeout=10).json()
            assert public["uploader"] == "同步后的昵称" and "username" not in public
            board = browser.get(
                api + "/charts/" + chart_id + "/leaderboard?difficulty=Oni", timeout=10
            ).json()
            assert board["items"][0]["nickname"] == "同步后的昵称"
            self.stdout.write("PASS SSO nickname changes reflected in web, game, chart and leaderboard")
            security = browser.get(issuer + "/security/", timeout=10)
            replacement = secrets.token_urlsafe(24)
            response = post_form(
                security,
                {"old_password": password, "new_password1": replacement, "new_password2": replacement},
            )
            assert "密码已更新" in response.text
            assert browser.get(api + "/me", timeout=10).json()["user"] is None
            assert game.get(api + "/game/bootstrap", headers=auth, timeout=10).status_code == 401
            assert (
                game.post(
                    api + "/game/login", json={"username": user.username, "password": password}, timeout=10
                ).status_code
                == 401
            )
            password = replacement
            session = login()
            response = browser.post(
                api + "/auth/logout",
                headers={"Origin": origin, "X-CSRF-Token": session["csrfToken"]},
                json={},
                timeout=10,
            )
            assert response.status_code == 200
            assert browser.get(api + "/me", timeout=10).json()["user"] is None
            self.stdout.write(
                "PASS password change revokes both web/game sessions; re-login and local logout"
            )
        finally:
            # Delete only this generated subject's data from the dedicated local copy.
            paths = []
            with psycopg.connect(config["DATABASE_URL"]) as db:
                if chart_id:
                    files = db.execute(
                        "SELECT f.id,f.storage_key FROM files f JOIN chart_versions v ON f.id IN (v.tja_file_id,v.audio_file_id) WHERE v.chart_id=%s",
                        [chart_id],
                    ).fetchall()
                    paths = [row[1] for row in files]
                    db.execute("DELETE FROM scores WHERE song_id=%s", [chart_id])
                    db.execute("DELETE FROM upload_requests WHERE chart_id=%s", [chart_id])
                    db.execute("DELETE FROM chart_versions WHERE chart_id=%s", [chart_id])
                    db.execute("DELETE FROM charts WHERE id=%s", [chart_id])
                    for file_id, _ in files:
                        db.execute("DELETE FROM files WHERE id=%s", [file_id])
                db.execute("DELETE FROM users WHERE id=%s", [user.pk])
            storage = Path(config["STORAGE_DIR"]).resolve()
            for key in paths:
                path = (storage / key).resolve()
                if path.is_relative_to(storage):
                    path.unlink(missing_ok=True)
            from django.contrib.sessions.models import Session

            Session.objects.filter(
                session_key__in=BrowserSession.objects.filter(user=user).values_list("session_key", flat=True)
            ).delete()
            user.delete()
            browser.close()
            self.stdout.write("Temporary integration records removed.")
