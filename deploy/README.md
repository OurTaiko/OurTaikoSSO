# 1Panel production deployment

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
FANMADE_SERVICE_KEY=<64 random hex characters>
FANMADE_OIDC_CLIENT_ID=ourtaiko-fanmade-web
FANMADE_OIDC_CLIENT_SECRET=<strong random secret>
FANMADE_REDIRECT_URI=https://fanmade.ourtaiko.org/api/v1/auth/sso/callback
```

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
