"""Reproducible local-only upgrade/backup-restore drill with synthetic data.

Build images first (see docs/APPLICATION-MIGRATION.md). This script never uses
SSH, production env, or another Compose project. Existing lab data is refused.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".data/unification-lab"
DOCKER = shutil.which("docker") or "/Users/kirisamevanilla/.docker/bin/docker"
BEFORE = "ourtaiko-sso:unification-before"
AFTER = "ourtaiko-sso:unification-dev"
PG = "ourtaiko-unification-lab-postgres-1"
NETWORK = "ourtaiko-unification-lab_default"


def run(args, *, body=None, ok=True):
    result = subprocess.run([DOCKER, *args], input=body, capture_output=True)
    if ok and result.returncode:
        (LAB / "last-error.log").write_bytes(result.stdout + result.stderr)
        raise RuntimeError("Local lab command failed; inspect .data/unification-lab/last-error.log")
    return result


def container(image, command, *, env="sso.env", body=None, ok=True):
    return run(
        [
            "run",
            "--rm",
            "-i",
            "--network",
            NETWORK,
            "--env-file",
            str(LAB / env),
            "-e",
            "OIDC_PRIVATE_KEY_FILE=/app/.data/oidc-private.pem",
            "-v",
            f"{LAB}/oidc-private.pem:/app/.data/oidc-private.pem:ro",
            image,
            *command,
        ],
        body=body,
        ok=ok,
    )


def shell(image, code, env="sso.env"):
    return container(
        image, ["python", "manage.py", "shell", "--verbosity", "0"], env=env, body=code.encode()
    ).stdout


SEED = """
from datetime import timedelta
from django.utils import timezone
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
from oauth2_provider.models import Application, AccessToken, RefreshToken, Grant
from accounts.models import User, GameClient, ClientRole, GameSession
from accounts.security import digest
assert User.objects.count() == 0 and GameClient.objects.count() == 0
u=User.objects.create_superuser(username="labadmin",nickname="Local migration admin",password="LocalOnly-Unification-4279")
app=Application.objects.create(name="Fanmade (lab)",client_id="fanmade-web",client_secret="c"*64,
    client_type="confidential",authorization_grant_type="authorization-code",algorithm="RS256",
    redirect_uris="http://127.0.0.1:28091/callback")
Application.objects.create(name="HachiCats (lab)",client_id="hachicats-web",client_secret="d"*64,
    client_type="confidential",authorization_grant_type="authorization-code",algorithm="RS256",
    redirect_uris="http://127.0.0.1:28092/callback")
f=GameClient.objects.create(id="fanmade",name="Fanmade",secret_hash=digest("a"*64),web_client_id=app.client_id)
e=GameClient.objects.create(id="ese",name="ESE (lab)",secret_hash=digest("b"*64))
GameClient.objects.create(id="disabled",name="Disabled (lab)",secret_hash=digest("f"*64),enabled=False)
ClientRole.objects.create(user=u,client=f,is_admin=True)
ClientRole.objects.create(user=u,client=e,is_admin=False)
for client,token in [(f,"1"*64),(e,"2"*64)]:
    GameSession.objects.create(user=u,client=client,token_hash=digest(token),auth_version=u.auth_version,
        expires_at=timezone.now()+timedelta(days=7))
t=AccessToken.objects.create(application=app,user=u,token="synthetic-web-access",scope="openid profile",
    expires=timezone.now()+timedelta(days=1))
RefreshToken.objects.create(application=app,user=u,token="synthetic-refresh",access_token=t)
s=SessionStore(); s[SESSION_KEY]=str(u.pk); s[BACKEND_SESSION_KEY]="django.contrib.auth.backends.ModelBackend"
s[HASH_SESSION_KEY]=u.get_session_auth_hash(); s.save()
print("Synthetic fixtures created")
"""

FINGERPRINT = """
import json, hashlib
from django.db import connection
with connection.cursor() as c:
    c.execute("SET TIME ZONE 'UTC'")
    c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    tables=[r[0] for r in c.fetchall()]
    result={}
    for table in tables:
        c.execute('SELECT row_to_json(t)::text FROM "'+table+'" t ORDER BY row_to_json(t)::text COLLATE "C"')
        rows=[r[0] for r in c.fetchall()]
        result[table]={'count':len(rows),'sha256':hashlib.sha256('\\n'.join(rows).encode()).hexdigest()}
print(json.dumps(result,sort_keys=True))
"""

APP_FINGERPRINT = """
import json
from django.core.serializers import serialize
from oauth2_provider.models import Application
print(serialize('json',Application.objects.filter(client_id__in=['fanmade-web','hachicats-web']).order_by('pk')))
"""

VERIFY = """
from django.test import Client
from django.contrib.sessions.models import Session
from oauth2_provider.models import Application
from accounts.models import ApplicationAccess, ClientRole, GameSession
from accounts.security import digest
c=Client()
def api(path,service,key,body):
    r=c.post('/internal/v1/'+path,body,content_type='application/json',HTTP_X_SERVICE_ID=service,HTTP_X_SERVICE_KEY=key)
    assert r.status_code==200,(path,r.status_code)
    return r.json()
f=Application.objects.get(client_id='fanmade-web')
e=Application.objects.get(client_id='ese')
assert Application.objects.filter(name='Fanmade (lab)').count()==1
assert ClientRole.objects.get(client=f).is_admin
assert not ClientRole.objects.get(client=e).is_admin
assert GameSession.objects.get(token_hash=digest('1'*64)).client==f
assert GameSession.objects.get(token_hash=digest('2'*64)).client==e
for service,key in [('fanmade','a'*64),('fanmade-web','c'*64)]:
    assert api('game/introspect',service,key,{'accessToken':'1'*64})['user']['isAdmin']
    assert api('web/introspect',service,key,{'accessToken':'synthetic-web-access'})['user']['isAdmin']
assert not api('game/introspect','ese','b'*64,{'accessToken':'2'*64})['user']['isAdmin']
h=Application.objects.get(client_id='hachicats-web')
assert not ApplicationAccess.objects.get(application=h).allow_web
assert not ApplicationAccess.objects.get(legacy_service_id='disabled').enabled
c.cookies['ourtaiko_sso_session']=Session.objects.get().session_key
assert c.get('/admin/oauth2_provider/application/').status_code==200
assert c.get('/admin/accounts/gameclient/').status_code==404
print('PASS: migrated roles, existing game/OAuth/browser sessions, legacy and canonical auth')
"""


def variant(name):
    data = (LAB / "sso.env").read_text()
    lines = [
        line.rsplit("/", 1)[0] + "/" + name if line.startswith("DATABASE_URL=") else line
        for line in data.splitlines()
    ]
    (LAB / (name + ".env")).write_text("\n".join(lines) + "\n")
    run(["exec", PG, "createdb", "-U", "postgres", name])
    run(
        ["exec", "-i", PG, "pg_restore", "-U", "postgres", "-d", name, "--exit-on-error"],
        body=(LAB / "before.dump").read_bytes(),
    )
    return name + ".env"


def main():
    os.umask(0o077)
    if (LAB / "before.dump").exists():
        baseline = json.loads((LAB / "before-fingerprints.json").read_text())
        assert json.loads(shell(BEFORE, FINGERPRINT)) == baseline, (
            "Existing lab changed; refusing to overwrite"
        )
    else:
        # Refuse an existing database rather than silently altering it.
        result = run(
            [
                "exec",
                PG,
                "psql",
                "-U",
                "postgres",
                "-d",
                "sso",
                "-Atc",
                "SELECT count(*) FROM pg_tables WHERE schemaname='public'",
            ]
        ).stdout.strip()
        assert result == b"0", "Lab database must be empty"
        container(BEFORE, ["python", "manage.py", "migrate", "--noinput"])
        shell(BEFORE, SEED)
        baseline = json.loads(shell(BEFORE, FINGERPRINT))
        (LAB / "before-fingerprints.json").write_text(json.dumps(baseline, indent=2))
        (LAB / "before.dump").write_bytes(
            run(["exec", PG, "pg_dump", "-U", "postgres", "-d", "sso", "-Fc"]).stdout
        )
    print("PASS: old schema fixtures and consistent backup", flush=True)
    container(AFTER, ["python", "manage.py", "check_application_migration"])
    original_apps = shell(BEFORE, APP_FINGERPRINT)
    container(AFTER, ["python", "manage.py", "migrate", "--noinput"])
    assert shell(AFTER, APP_FINGERPRINT) == original_apps, "Existing OAuth application fields changed"
    after = json.loads(shell(AFTER, FINGERPRINT))
    for table in baseline:
        if table not in {
            "accounts_clientrole",
            "accounts_gamesession",
            "django_migrations",
            "django_content_type",
            "auth_permission",
            "oauth2_provider_application",
        }:
            assert baseline[table] == after[table], table
    print(shell(AFTER, VERIFY).decode().strip(), flush=True)
    # Restore to an independent database and actually boot the OLD code against it.
    restored = variant("sso_restore")
    assert json.loads(shell(BEFORE, FINGERPRINT, restored)) == baseline
    container(BEFORE, ["python", "manage.py", "check"], env=restored)
    old_verify = VERIFY[VERIFY.index("c=Client()") : VERIFY.index("f=Application.objects.get")]
    shell(
        BEFORE,
        "from django.test import Client\n"
        + old_verify
        + "assert api('game/introspect','fanmade','a'*64,{'accessToken':'1'*64})['user']['isAdmin']\n",
        restored,
    )
    print("PASS: backup restored, all table fingerprints equal, old code authenticates", flush=True)
    conflict = variant("sso_conflict")
    shell(
        BEFORE,
        "from accounts.models import GameClient\nGameClient.objects.create(id='duplicate',name='Duplicate',secret_hash='f'*64,web_client_id='fanmade-web')",
        conflict,
    )
    r = container(AFTER, ["python", "manage.py", "migrate", "--noinput"], env=conflict, ok=False)
    assert r.returncode != 0 and b"multiple services" in r.stderr
    shell(
        BEFORE,
        "from django.db import connection\nassert 'accounts_applicationaccess' not in connection.introspection.table_names()",
        conflict,
    )
    print("PASS: ambiguous mapping blocked and migration rolled back atomically", flush=True)
    (LAB / "verification.json").write_text(
        json.dumps(
            {
                "migration": "passed",
                "restore": "passed",
                "conflict_rollback": "passed",
                "data": "synthetic only",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
