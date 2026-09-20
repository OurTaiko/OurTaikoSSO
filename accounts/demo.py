"""Local-only relying-party demonstration. Never mounted with DEBUG=False."""

import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from jwcrypto import jwk, jwt


def start(request):
    verifier = secrets.token_urlsafe(48)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    request.session["oidc_demo"] = {"state": state, "nonce": nonce, "verifier": verifier}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return redirect(
        settings.SSO_BASE_URL
        + "/o/authorize/?"
        + urlencode(
            {
                "client_id": "ourtaiko-dev-web",
                "response_type": "code",
                "scope": "openid profile",
                "redirect_uri": settings.SSO_BASE_URL + "/dev/demo/callback/",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def callback(request):
    attempt = request.session.pop("oidc_demo", None)
    if not attempt or not secrets.compare_digest(attempt["state"], request.GET.get("state", "")):
        return HttpResponseBadRequest("登录状态不匹配，请重新开始。")
    if request.GET.get("error") or not request.GET.get("code"):
        return render(
            request, "accounts/notice.html", {"title": "已取消授权", "text": "你没有向演示应用授权任何资料。"}
        )
    config = json.loads((settings.BASE_DIR / ".data/dev-clients.json").read_text())["webDemo"]
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(
                settings.SSO_BASE_URL + "/o/token/",
                data={
                    "grant_type": "authorization_code",
                    "code": request.GET["code"],
                    "redirect_uri": settings.SSO_BASE_URL + "/dev/demo/callback/",
                    "code_verifier": attempt["verifier"],
                },
                auth=(config["clientId"], config["clientSecret"]),
                timeout=10,
            )
            response.raise_for_status()
            tokens = response.json()
            public_key = jwk.JWK.from_pem(settings.OAUTH2_PROVIDER["OIDC_RSA_PRIVATE_KEY"].encode())
            public_key = jwk.JWK.from_json(public_key.export_public())
            verified = jwt.JWT(
                jwt=tokens["id_token"],
                key=public_key,
                algs=["RS256"],
                check_claims={
                    "iss": settings.SSO_BASE_URL,
                    "aud": config["clientId"],
                    "nonce": attempt["nonce"],
                    "exp": None,
                },
            )
            claims = json.loads(verified.claims)
            response = client.get(
                settings.SSO_BASE_URL + "/o/userinfo/",
                headers={
                    "Authorization": "Bearer " + tokens["access_token"],
                },
                timeout=10,
            )
            response.raise_for_status()
            profile = response.json()
            if profile["sub"] != claims["sub"]:
                raise ValueError("subject mismatch")
    except (requests.RequestException, ValueError, KeyError, jwt.JWException):
        return HttpResponseBadRequest("授权验证失败，请重新开始。")
    return render(request, "accounts/demo.html", {"profile": profile})
