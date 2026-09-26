"""Finish the synthetic Docker lab's stage-one -> application-only upgrade.

Requires the stage-one drill and HTTP credential cutover to have completed.
Stops only this dedicated Compose project's writers; refuses to repeat cleanup.
"""

import json

from unification_lab import AFTER, FINGERPRINT, LAB, ROOT, container, run, shell

FINAL = "ourtaiko-sso:unification-dev"
COMPOSE = ["compose", "-f", str(ROOT / "deploy/compose.unification-lab.yml")]
POLICY = """
import json
from accounts.models import ApplicationAccess
print(json.dumps(list(ApplicationAccess.objects.order_by('pk').values('id','application_id','enabled','allow_game','allow_profiles','allow_web'))))
"""


def main():
    backup = LAB / "before-cleanup.dump"
    if backup.exists():
        raise RuntimeError("Cleanup backup exists; refusing to overwrite or repeat migration")
    run([*COMPOSE, "stop", "fanmade", "sso"])
    before = json.loads(shell(AFTER, FINGERPRINT))
    assert "accounts_gameclient" in before
    backup.write_bytes(
        run(
            ["exec", "ourtaiko-unification-lab-postgres-1", "pg_dump", "-U", "postgres", "-d", "sso", "-Fc"]
        ).stdout
    )
    # At least one synthetic service still accepts the old credential.
    shell(
        AFTER,
        "from accounts.models import ApplicationAccess\nassert ApplicationAccess.objects.filter(accept_legacy=True).exists()",
    )
    refused = container(FINAL, ["python", "manage.py", "migrate", "--noinput"], ok=False)
    assert refused.returncode and b"Removal blocked" in refused.stderr
    assert json.loads(shell(AFTER, FINGERPRINT)) == before
    print("PASS: active legacy credentials block cleanup, with no database changes", flush=True)
    # Only synthetic services: provision a known canonical key for backend-only fixtures.
    shell(
        AFTER,
        """
from accounts.models import ApplicationAccess
from oauth2_provider.models import Application
for name in ('ese','disabled'):
    app=Application.objects.get(client_id=name)
    app.client_secret='Synthetic-Cleanup-Only-'*3
    app.save(update_fields=['client_secret'])
ApplicationAccess.objects.update(accept_legacy=False)
""",
    )
    before = json.loads(shell(AFTER, FINGERPRINT))
    policy = shell(AFTER, POLICY)
    container(FINAL, ["python", "manage.py", "migrate", "--noinput"])
    after = json.loads(shell(FINAL, FINGERPRINT))
    assert "accounts_gameclient" not in after
    for table, value in before.items():
        if table not in {"accounts_gameclient", "accounts_applicationaccess", "django_migrations"}:
            assert after[table] == value, table
    assert shell(FINAL, POLICY) == policy
    shell(
        FINAL,
        """
from django.db import connection
with connection.cursor() as c:
    c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='accounts_applicationaccess'")
    assert not {'accept_legacy','legacy_service_id','legacy_secret_hash'} & {r[0] for r in c.fetchall()}
""",
    )
    print(
        "PASS: legacy table and fields removed; identities, roles, capabilities and sessions preserved",
        flush=True,
    )
    run([*COMPOSE, "up", "-d", "--wait", "sso", "fanmade"])
    (LAB / "cleanup-verification.json").write_text(
        json.dumps({"guard": "passed", "preservation": "passed", "schema": "passed"})
    )


if __name__ == "__main__":
    main()
