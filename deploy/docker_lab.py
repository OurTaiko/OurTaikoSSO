"""Local-only raw -> Docker -> fresh Docker restore drill. Never targets production.

Run with this repository's .venv/bin/python. Bundles contain development secrets;
keep them in .data, never commit or publish them. No source database writes.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from dotenv import dotenv_values

SSO = Path(__file__).resolve().parents[1]
FAN = SSO.parent / "Fanmade/backend"
LAB = SSO / ".data/docker-lab"
DOCKER = shutil.which("docker") or "/Applications/Docker.app/Contents/Resources/bin/docker"
TABLES = {
    "sso": ["accounts_user"],
    "fanmade": ["users", "charts", "chart_versions", "scores", "files", "chart_covers"],
}


def run(args, **kwargs):
    result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        # Never interpolate commands, SQL, URLs or env into the error message.
        raise RuntimeError(
            f"{Path(str(args[0])).name} failed (exit {result.returncode}); inspect local service health"
        )
    return result.stdout


def read_env(path):
    return dict(dotenv_values(path, interpolate=False))


def write_env(path, values):
    if any("\n" in str(v) or "\r" in str(v) for v in values.values()):
        raise ValueError("Multiline env values are unsupported")
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    path.chmod(0o600)


def hashes(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are not supported in migration files")
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
    return result


def fingerprint(connection, tables):
    connection.execute("SET TIME ZONE 'UTC'")
    result = {}
    for table in tables:
        rows = connection.execute(
            psycopg.sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(
                psycopg.sql.Identifier(table)
            )
        ).fetchall()
        result[table] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(sorted(r[0] for r in rows)).encode()).hexdigest(),
        }
    return result


def archive(source, target):
    hashes(source)  # Refuse symlinks before packing.
    with tarfile.open(target, "w:gz") as tar:
        tar.add(source, arcname=".")


def seal(bundle):
    (bundle / "manifest.json").write_text(json.dumps(hashes(bundle), indent=2))


def check_bundle(bundle):
    expected = json.loads((bundle / "manifest.json").read_text())
    actual = hashes(bundle)
    actual.pop("manifest.json", None)
    if actual != expected:
        raise RuntimeError("Bundle checksum mismatch")


def prepare(bundle):
    if bundle.exists():
        raise RuntimeError("Bundle already exists; choose a new name")
    sso, fan = read_env(SSO / ".env"), read_env(FAN / ".env")
    sources = {"sso": sso, "fanmade": fan}
    for key, config in sources.items():
        u = urlsplit(config["DATABASE_URL"])
        expected = "/ourtaiko_sso_dev" if key == "sso" else "/ourtaiko_fanmade_sso_dev"
        if u.hostname not in {"localhost", "127.0.0.1"} or u.path != expected:
            raise RuntimeError("Only the documented local development databases are allowed")
    if sso.get("DJANGO_DEBUG") != "true":
        raise RuntimeError("Only DEBUG development sources are allowed")
    # Avoid copying files while the known raw services can accept writes.
    for url in [sso["SSO_BASE_URL"], "http://" + fan.get("LISTEN_ADDR", "127.0.0.1:8080")]:
        u = urlsplit(url)
        if u.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Only loopback raw services are allowed")
        with socket.socket() as sock:
            if sock.connect_ex((u.hostname, u.port)) == 0:
                raise RuntimeError(
                    "Stop the raw development SSO/Fanmade servers before preparing a consistent bundle"
                )
    bundle.mkdir(parents=True, mode=0o700)
    baseline = {}
    for key, config in sources.items():
        folder = bundle / key
        folder.mkdir(mode=0o700)
        env = dict(os.environ, PGOPTIONS="-c default_transaction_read_only=on")
        mapping = {
            "dbname": "PGDATABASE",
            "host": "PGHOST",
            "port": "PGPORT",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "sslmode": "PGSSLMODE",
        }
        env.update(
            {
                mapping[k]: v
                for k, v in psycopg.conninfo.conninfo_to_dict(config["DATABASE_URL"]).items()
                if k in mapping
            }
        )
        run(["pg_dump", "-Fc", "--no-owner", "--no-acl", "-f", folder / "database.dump"], env=env)
        with psycopg.connect(config["DATABASE_URL"], options="-c default_transaction_read_only=on") as db:
            baseline[key] = fingerprint(db, TABLES[key])
    key = Path(sso.get("OIDC_PRIVATE_KEY_FILE", ".data/oidc-private.pem"))
    if not key.is_absolute():
        key = SSO / key
    data = bundle / "sso/data"
    data.mkdir(mode=0o700)
    shutil.copyfile(key, data / "oidc-private.pem")
    archive(data, bundle / "sso/data.tar.gz")
    shutil.rmtree(data)
    storage = Path(fan["STORAGE_DIR"])
    if not storage.is_absolute():
        storage = FAN / storage
    baseline["files"] = hashes(storage)
    baseline["signing_key"] = hashlib.sha256(key.read_bytes()).hexdigest()
    archive(storage, bundle / "fanmade/files.tar.gz")
    passwords = {key: secrets.token_hex(32) for key in ["postgres", "sso", "fanmade"]}
    (bundle / "roles.json").write_text(json.dumps(passwords))
    write_env(
        bundle / "postgres.env",
        {"POSTGRES_PASSWORD": passwords["postgres"], "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
    )
    sso_env = {
        "DJANGO_DEBUG": "true",
        "DJANGO_SECRET_KEY": sso["DJANGO_SECRET_KEY"],
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "SSO_BASE_URL": "http://127.0.0.1:18090",
        "DATABASE_URL": f"postgres://ourtaiko_sso:{passwords['sso']}@postgres:5432/ourtaiko_sso",
        "EMAIL_BACKEND": "django.core.mail.backends.filebased.EmailBackend",
        "TRUST_PROXY_HEADERS": "false",
        "FANMADE_SERVICE_KEY": fan["SSO_SERVICE_KEY"],
        "FANMADE_OIDC_CLIENT_ID": fan["SSO_CLIENT_ID"],
        "FANMADE_OIDC_CLIENT_SECRET": fan["SSO_CLIENT_SECRET"],
        "FANMADE_REDIRECT_URI": "http://127.0.0.1:18080/api/v1/auth/sso/callback",
    }
    write_env(bundle / "sso/env", sso_env)
    fan_env = {
        k: fan[k]
        for k in [
            "SSO_SERVICE_ID",
            "SSO_SERVICE_KEY",
            "SSO_CLIENT_ID",
            "SSO_CLIENT_SECRET",
            "SESSION_ENCRYPTION_KEY",
        ]
    }
    fan_env.update(
        DATABASE_URL=f"postgres://ourtaiko_fanmade:{passwords['fanmade']}@postgres:5432/ourtaiko_fanmade",
        APP_ORIGIN="http://127.0.0.1:18080",
        COOKIE_SECURE="false",
        SSO_ISSUER="http://127.0.0.1:18090",
        SSO_CONNECT_ADDRESS="sso:8090",
        SSO_REDIRECT_URL=sso_env["FANMADE_REDIRECT_URI"],
        SSO_MIGRATION_BACKUP_CONFIRMED="false",
    )
    write_env(bundle / "fanmade/env", fan_env)
    (bundle / "baseline.json").write_text(json.dumps(baseline, indent=2))
    seal(bundle)
    print("Prepared separate SSO and Fanmade data packages from read-only local sources.", flush=True)


class Stack:
    def __init__(self, name):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
            raise ValueError("Invalid lab name")
        self.name = name
        self.path = LAB / name
        self.sso = "ourtaiko-lab-" + name + "-sso"
        self.fan = "ourtaiko-lab-" + name + "-fanmade"
        self.env = dict(
            os.environ,
            STACK_DIR=str(self.path),
            SSO_PROJECT=self.sso,
            FANMADE_PROJECT=self.fan,
            OURTAIKO_NETWORK="ourtaiko-lab-" + name,
        )
        self.env["PATH"] = str(Path(DOCKER).parent) + os.pathsep + self.env.get("PATH", "")

    def compose(self, app, *args, **kwargs):
        config = SSO / "deploy/compose.stack.yml" if app == "sso" else FAN / "deploy/compose.yml"
        return run([DOCKER, "compose", "-f", config, *args], env=self.env, **kwargs)

    def pg(self, *args, **kwargs):
        return self.compose("sso", "exec", "-T", "postgres", *args, **kwargs)

    def import_volume(self, volume, source, image):
        run(
            [
                DOCKER,
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "DAC_OVERRIDE",
                "--cap-add",
                "FOWNER",
                "-v",
                f"{volume}:/restore",
                "-v",
                f"{source.parent}:/backup:ro",
                "--entrypoint",
                "sh",
                image,
                "-c",
                'tar xzf "/backup/$1" -C /restore && chown -R 10001:10001 /restore',
                "sh",
                source.name,
            ]
        )

    def restore(self, bundle):
        check_bundle(bundle)
        if self.path.exists():
            raise RuntimeError("Target lab exists; restoration only supports a fresh target")
        for project in [self.sso, self.fan]:
            if (
                run([DOCKER, "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).strip()
                or run(
                    [DOCKER, "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]
                ).strip()
            ):
                raise RuntimeError("Target Docker resources already exist; refusing to overwrite")
        self.path.mkdir(parents=True, mode=0o700)
        for source, target in [
            ("sso/env", "sso.env"),
            ("fanmade/env", "fanmade.env"),
            ("postgres.env", "postgres.env"),
            ("roles.json", "roles.json"),
            ("baseline.json", "baseline.json"),
        ]:
            shutil.copyfile(bundle / source, self.path / target)
        write_env(
            self.path / "compose.env",
            {k: self.env[k] for k in ["STACK_DIR", "SSO_PROJECT", "FANMADE_PROJECT", "OURTAIKO_NETWORK"]},
        )
        self.compose("sso", "config", "--quiet")
        self.compose("fanmade", "config", "--quiet")
        print("Starting shared PostgreSQL in the SSO stack...", flush=True)
        self.compose("sso", "up", "-d", "--wait", "postgres")
        passwords = json.loads((self.path / "roles.json").read_text())
        for app in ["sso", "fanmade"]:
            role = "ourtaiko_" + app
            password = passwords[app]
            if not re.fullmatch(r"[0-9a-f]{64}", password):
                raise ValueError("Invalid lab role password")
            sql = f"CREATE ROLE {role} LOGIN PASSWORD '{password}'; CREATE DATABASE {role} OWNER {role}; REVOKE CONNECT ON DATABASE {role} FROM PUBLIC; GRANT CONNECT ON DATABASE {role} TO {role};"
            self.pg(
                "psql", "-X", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1", input=sql.encode()
            )
            with (bundle / app / "database.dump").open("rb") as dump:
                self.pg(
                    "pg_restore",
                    "-U",
                    "postgres",
                    "-d",
                    role,
                    "--role",
                    role,
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                    stdin=dump,
                )
        self.finish_restore(bundle)

    def finish_restore(self, bundle):
        self.compose("sso", "create", "sso")
        self.import_volume(self.sso + "_sso-data", bundle / "sso/data.tar.gz", "ourtaiko-sso:local")
        # Only the restored copy gets a Docker-local browser callback.
        code = "import os; from oauth2_provider.models import Application; Application.objects.filter(client_id=os.environ['FANMADE_OIDC_CLIENT_ID']).update(redirect_uris=os.environ['FANMADE_REDIRECT_URI'])"
        self.compose("sso", "run", "--rm", "--no-deps", "sso", "python", "manage.py", "migrate", "--noinput")
        self.compose("sso", "run", "--rm", "--no-deps", "sso", "python", "manage.py", "shell", "-c", code)
        self.compose("sso", "run", "--rm", "--no-deps", "sso", "python", "manage.py", "provision_fanmade")
        self.compose("sso", "up", "-d", "--wait", "sso")
        print(
            "SSO restored and healthy; restoring Fanmade files and starting its separate stack...", flush=True
        )
        self.compose("fanmade", "create", "fanmade")
        self.import_volume(self.fan + "_files", bundle / "fanmade/files.tar.gz", "ourtaiko-fanmade:local")
        self.compose("fanmade", "up", "-d", "--wait")
        self.verify()

    def connect(self, app):
        passwords = json.loads((self.path / "roles.json").read_text())
        return psycopg.connect(
            host="127.0.0.1",
            port=15433,
            user="ourtaiko_" + app,
            password=passwords[app],
            dbname="ourtaiko_" + app,
        )

    def verify(self):
        baseline = json.loads((self.path / "baseline.json").read_text())
        for app in ["sso", "fanmade"]:
            with self.connect(app) as db:
                actual = fingerprint(db, TABLES[app])
                if actual != baseline[app]:
                    raise RuntimeError(f"{app} table fingerprint differs from backup")
                other = "ourtaiko_" + ("fanmade" if app == "sso" else "sso")
                assert db.execute(
                    "SELECT has_database_privilege(current_user,%s,'CONNECT')", [other]
                ).fetchone() == (False,)
                assert db.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone() == (
                    False,
                )
            print(
                app + " restored rows: " + json.dumps({k: v["count"] for k, v in actual.items()}), flush=True
            )
        code = "import hashlib,json,pathlib; p=pathlib.Path('/data/files'); print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()}))"
        # Use the Python image as a read-only verifier for the Fanmade-owned volume.
        actual = json.loads(
            run(
                [
                    DOCKER,
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    "10001",
                    "-v",
                    f"{self.fan}_files:/data/files:ro",
                    "--entrypoint",
                    "python",
                    "ourtaiko-sso:local",
                    "-c",
                    code,
                ]
            )
        )
        if actual != baseline["files"]:
            raise RuntimeError("Restored audio/TJA checksums differ")
        key_hash = (
            self.compose(
                "sso",
                "exec",
                "-T",
                "sso",
                "python",
                "-c",
                "import hashlib; print(hashlib.sha256(open('/app/.data/oidc-private.pem','rb').read()).hexdigest())",
            )
            .decode()
            .strip()
        )
        if key_hash != baseline["signing_key"]:
            raise RuntimeError("SSO signing key changed")
        print(
            f"PASS: independent roles, stable account/business rows, signing key and {len(actual)} file checksums.",
            flush=True,
        )

    def backup(self, bundle):
        if bundle.exists():
            raise RuntimeError("Bundle exists; refusing overwrite")
        bundle.mkdir(parents=True, mode=0o700)
        self.compose("fanmade", "stop")
        self.compose("sso", "stop", "sso")
        try:
            baseline = json.loads((self.path / "baseline.json").read_text())
            for app, project, volume, filename in [
                ("sso", self.sso, "sso-data", "data.tar.gz"),
                ("fanmade", self.fan, "files", "files.tar.gz"),
            ]:
                folder = bundle / app
                folder.mkdir(mode=0o700)
                (folder / "database.dump").write_bytes(
                    self.pg(
                        "pg_dump", "-U", "postgres", "-d", "ourtaiko_" + app, "-Fc", "--no-owner", "--no-acl"
                    )
                )
                with self.connect(app) as db:
                    baseline[app] = fingerprint(db, TABLES[app])
                data = run(
                    [
                        DOCKER,
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--user",
                        "10001",
                        "-v",
                        f"{project}_{volume}:/backup:ro",
                        "--entrypoint",
                        "tar",
                        "ourtaiko-sso:local",
                        "czf",
                        "-",
                        "-C",
                        "/backup",
                        ".",
                    ]
                )
                (folder / filename).write_bytes(data)
                if app == "fanmade":
                    with tarfile.open(folder / filename) as tar:
                        baseline["files"] = {
                            m.name.removeprefix("./"): hashlib.sha256(tar.extractfile(m).read()).hexdigest()
                            for m in tar.getmembers()
                            if m.isfile()
                        }
                shutil.copyfile(self.path / (app + ".env"), folder / "env")
            for file in ["postgres.env", "roles.json"]:
                shutil.copyfile(self.path / file, bundle / file)
            (bundle / "baseline.json").write_text(json.dumps(baseline, indent=2))
            seal(bundle)
        finally:
            self.compose("sso", "up", "-d", "--wait", "sso")
            self.compose("fanmade", "up", "-d", "--wait")
        print("Consistent Docker backup completed; applications resumed.", flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "restore", "verify", "backup", "stop", "start"])
    parser.add_argument("--name", default="first")
    parser.add_argument("--bundle", default="raw-backup")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", args.bundle):
        parser.error("Bundle must be a simple local name")
    bundle = LAB / "bundles" / args.bundle
    stack = Stack(args.name)
    if args.action == "prepare":
        prepare(bundle)
    elif args.action == "restore":
        stack.restore(bundle)
    elif args.action == "verify":
        stack.verify()
    elif args.action == "backup":
        stack.backup(bundle)
    elif args.action == "stop":
        stack.compose("fanmade", "stop")
        stack.compose("sso", "stop")
    elif args.action == "start":
        stack.compose("sso", "up", "-d", "--wait")
        stack.compose("fanmade", "up", "-d", "--wait")


if __name__ == "__main__":
    main()
