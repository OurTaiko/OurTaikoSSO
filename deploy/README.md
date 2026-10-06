# Deployment

For the new portable **SSO + PostgreSQL** stack and the separately deployed
Fanmade backend, see [Docker migration drill](DOCKER.md). The shared database
Compose belongs to this repository. Production switched to `compose.production.yml`
on 2026-09-24; see [current production topology, backups and validation](PRODUCTION.md).
`compose.postgres.legacy.yml` preserves the pre-migration layout for reference only.
The instructions below describe the previous 1Panel host-network deployment.

## Previous 1Panel host-network deployment

Build the pinned dependency image with `docker build -t ourtaiko-sso:release .`.
Import `deploy/compose.yml` into **1Panel → Containers → Compose**. The service
uses host networking and binds only `127.0.0.1:8090`; expose HTTPS through a
1Panel reverse-proxy website. Configure the proxy to overwrite Host,
X-Forwarded-Proto and X-Real-IP. No development server is used.

Create `/etc/ourtaiko-sso/sso.env` (root-owned, 0600), and
`/var/lib/ourtaiko-sso` (owner 10001:10001, 0700). Required environment:

```dotenv
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<random persistent secret of at least 50 characters>
DJANGO_ALLOWED_HOSTS=<public hostname>
SSO_BASE_URL=https://<public hostname or hostname:port>
DATABASE_URL=postgres://ourtaiko_sso:<password>@127.0.0.1:5432/ourtaiko_sso
OIDC_PRIVATE_KEY_FILE=/app/.data/oidc-private.pem
TRUST_PROXY_HEADERS=true
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=<existing SMTP server>
EMAIL_PORT=587
EMAIL_HOST_USER=<SMTP account>
EMAIL_HOST_PASSWORD=<SMTP password>
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=OurTaiko <no-reply@example.com>
FANMADE_OIDC_CLIENT_ID=ourtaiko-fanmade-web
FANMADE_OIDC_CLIENT_SECRET=<strong random secret>
FANMADE_REDIRECT_URI=https://fanmade.ourtaiko.org/api/v1/auth/sso/callback
S3_BUCKET=<bucket>
AWS_REGION=<region, or "auto" for R2>
S3_ENDPOINT_URL=<only for S3-compatible providers>
S3_PREFIX=sso
AWS_ACCESS_KEY_ID=<key limited to s3:GetObject/PutObject/DeleteObject on sso/*>
AWS_SECRET_ACCESS_KEY=<secret>
```

Avatars are stored as WebP under `S3_PREFIX` (default `sso/`) and served through
the SSO, so the bucket does not need public read access. Production refuses to
start without `S3_BUCKET`. Uploads may be up to 5 MB: the reverse proxy for the
SSO site needs `client_max_body_size 6m;` (nginx/OpenResty default is 1 MB).

Generate a persistent RSA 2048-bit or larger private key in the mounted data
folder, owner 10001:10001, mode 0600. Use a separate PostgreSQL database and
non-superuser role. Run migrations and `provision_fanmade` in a one-off container
with the same environment/mount/network settings before starting Gunicorn.
Provisioning is idempotent and refuses to silently rotate existing secrets.

To import old users, stop legacy account writes and take a complete database
and file backup. Pass the old database URL only to the explicit
`import_fanmade` command via FANMADE_SOURCE_DATABASE_URL. Run dry-run then
`--apply`, verify all IDs and admin roles, then upgrade Fanmade backend to
migration 018 using the same service and OAuth client credentials. Do not
import local development accounts or keys to production. `seed_dev` is not
used in production. Existing browser/game sessions will require login again.

Back up both databases, application files, environment files and the OIDC key.
Fanmade rollback requires the pre-018 database and old application versions;
keep the SSO service/database available until rollback has been verified.
