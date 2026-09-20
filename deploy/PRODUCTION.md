# Production installation and pending cutover

The SSO container is managed by **1Panel → Containers → Compose → ourtaiko-sso**.
It runs the production Gunicorn image `ourtaiko-sso:release`, with a read-only
filesystem and a non-root user, listening on `127.0.0.1:8090`.

1Panel also owns a separate reverse-proxy website named `ourtaiko-sso`:
`https://fanmade.ourtaiko.org:8443`, using the existing Fanmade certificate.
The proxy sends `Host: fanmade.ourtaiko.org:8443` and overwrites the forwarded
scheme and client address. The operating-system firewall permits TCP 8443.
External access must be verified before enabling Fanmade's SSO login. At the
2026-09-20 installation, loopback and server-side HTTPS checks succeeded, while
external TCP connections timed out. Check the provider firewall/security group,
or arrange a dedicated DNS hostname on standard HTTPS port 443.

## Server files

- Source checkout: `/opt/ourtaiko-sso/src`
- 1Panel compose source: `/opt/ourtaiko-sso/docker-compose.yml`
- Environment: `/etc/ourtaiko-sso/sso.env` (root-only)
- Persistent signing key: `/var/lib/ourtaiko-sso/oidc-private.pem`
- Separate database and non-superuser role: `ourtaiko_sso`
- Prepared Fanmade environment: `/etc/ourtaiko-sso/fanmade-next.env`
- Read-only import connection: `/etc/ourtaiko-sso/import.env`
- Prepared new backend binary: `/tmp/ourtaiko-api-sso` (rebuild if absent)

SMTP uses the existing production mail service. TLS authentication was checked
without sending mail. A disposable SSO account verified real browser login and
nickname persistence through an SSH transport; the account and sessions were
removed afterwards. This check does not establish public network reachability.

The legacy Fanmade service remains active, with 7 accounts, 26 charts and
48 scores at inspection. No production accounts have been imported yet and
migration 018 has not run. Backend commit `5492b09` is pushed to `main`;
frontend commit `50bde22` is pushed to `feat/sso-auth`. Frontend `main` triggers
automatic production deployment, so advance it only after backend cutover.

## Cutover order

1. Verify the public SSO URL, trusted certificate, `/readyz`, login page and OIDC
   discovery from an external client. If changing the hostname, update SSO base
   URL/allowed hosts, Fanmade issuer, proxy Host, and certificate together.
2. Stop `ourtaiko-fanmade` to freeze legacy account writes, and create a **fresh**
   database dump plus application/configuration backup. The earlier validated
   snapshot at `/var/backups/ourtaiko-sso-migration/20260920T222537Z` was taken
   while the old service was running; do not use it as the final migration dump.
3. Run `import_fanmade` dry-run and `--apply` with both SSO and import environment
   files. Verify every source ID, username, nickname, bcrypt hash, email,
   verification flag and admin role before dropping legacy account columns.
4. Install the prepared Fanmade environment with the same root:ourtaiko owner
   and mode 0640 as the old environment, install the new backend binary, then
   start `ourtaiko-fanmade`. Migration 018 checks all existing IDs against SSO
   before committing. Verify `/readyz`, charts and score ownership/counts.
5. Advance frontend `main` to the verified SSO commit and push. Watch the existing
   GitHub Actions deploy job and confirm the new 1Panel release symlink.
6. Verify browser OIDC callback/logout and account-center links, plus legacy
   game login/token validation/score operations. Use a disposable test account
   and remove only its data afterwards. Old sessions require login again.
7. Back up both final databases and retain the private configuration/signing key.

If migration fails, keep the frontend on its old release. Restore the old backend
binary and environment; if migration 018 committed, restore the fresh pre-cutover
Fanmade dump while the API is stopped, before restarting the old binary. Keep
SSO data and all original backups until rollback has been verified.
